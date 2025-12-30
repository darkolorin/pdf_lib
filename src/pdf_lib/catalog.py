from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import db as db_mod
from .categorizer import categorize, config_uses_text
from .llm import LLMError, llm_classify_category
from .library import Library
from .metadata import mdls_basic, mdls_text_sample
from .organize import build_categorized_view
from .util import now_ts


def _latest_source_maps(conn) -> tuple[dict[str, str], dict[str, str]]:
    """
    Returns:
      - latest_source_path_by_hash
      - latest_source_basename_by_hash
    """
    latest_path: dict[str, str] = {}
    latest_base: dict[str, str] = {}
    rows = conn.execute(
        """
        SELECT hash, source_path, source_basename
        FROM source_files
        WHERE hash IS NOT NULL AND status = 'ok'
        ORDER BY last_seen_at DESC
        """
    ).fetchall()
    for r in rows:
        h = r["hash"]
        if h not in latest_path:
            latest_path[h] = r["source_path"]
        if h not in latest_base:
            latest_base[h] = r["source_basename"] or Path(r["source_path"]).name
    return latest_path, latest_base


def categorize_library(
    *,
    library: Library,
    config_path: Path | None,
    link_mode: str,
    refresh_view: bool,
    recategorize_all: bool,
    text_sample_bytes: int,
    llm_provider: str = "off",
    llm_model: str | None = None,
    llm_mode: str = "fallback",
    llm_min_confidence: float = 0.6,
    llm_timeout_seconds: float = 30.0,
    llm_max_output_tokens: int = 200,
    llm_path_mode: str = "tail",
    llm_path_tail_parts: int = 3,
    verbose: bool = False,
) -> dict[str, int]:
    library.ensure_initialized()

    cfg = library.load_categories_config(config_path)
    default_category = cfg.get("default_category", "Unsorted")
    min_score = float(cfg.get("min_score", 4))

    # Categories list (for LLM selection + validation).
    categories: list[str] = []
    for c in cfg.get("categories", []):
        name = str(c.get("name", "")).strip()
        if name:
            categories.append(name)

    use_text_rules = config_uses_text(cfg)

    llm_provider = (llm_provider or "off").lower().strip()
    llm_mode = (llm_mode or "fallback").lower().strip()
    llm_path_mode = (llm_path_mode or "tail").lower().strip()

    if llm_provider not in {"off", "uzu"}:
        raise ValueError("llm_provider must be one of: off, uzu")
    if llm_mode not in {"fallback", "always"}:
        raise ValueError("llm_mode must be one of: fallback, always")
    if llm_path_mode not in {"basename", "tail", "full"}:
        raise ValueError("llm_path_mode must be one of: basename, tail, full")

    if llm_provider == "uzu":
        # The model is selected when you start the uzu server; this is mostly informative.
        llm_model = llm_model or os.environ.get("UZU_MODEL") or "qwen3-4b"

    use_text_llm = llm_provider != "off"
    use_text = (use_text_rules or use_text_llm) and text_sample_bytes > 0 and shutil.which("mdls") is not None

    conn = db_mod.connect(library.db_path)
    try:
        db_mod.init_schema(conn)

        latest_source_path_by_hash, latest_source_basename_by_hash = _latest_source_maps(conn)

        where = "" if recategorize_all else "(category IS NULL OR category = '')"
        docs_to_cat = db_mod.iter_documents(conn, where_sql=where)

        updated = 0
        llm_calls = 0
        llm_used = 0
        llm_failed = 0
        now = now_ts()

        for doc in docs_to_cat:
            hash_hex = doc["hash"]
            vault_path = library.root / doc["vault_relpath"]

            src_path = latest_source_path_by_hash.get(hash_hex)
            src_base = latest_source_basename_by_hash.get(hash_hex) or vault_path.name

            md = mdls_basic(vault_path) if shutil.which("mdls") is not None else {}

            page_count = md.get("kMDItemNumberOfPages")
            if isinstance(page_count, float):
                page_count = int(page_count)

            title = md.get("kMDItemTitle")
            subject = md.get("kMDItemSubject")

            authors_val = md.get("kMDItemAuthors")
            if isinstance(authors_val, list):
                authors = ", ".join(str(a) for a in authors_val)
            else:
                authors = str(authors_val) if authors_val else None

            keywords_val = md.get("kMDItemKeywords")
            if isinstance(keywords_val, list):
                keywords = ", ".join(str(k) for k in keywords_val)
            else:
                keywords = str(keywords_val) if keywords_val else None

            text_sample = mdls_text_sample(vault_path, max_bytes=text_sample_bytes) if use_text else None

            meta_json = None
            if md:
                try:
                    meta_json = json.dumps(md, ensure_ascii=False, sort_keys=True)
                except Exception:
                    meta_json = None

            db_mod.update_document_metadata(
                conn,
                hash_hex=hash_hex,
                page_count=page_count if isinstance(page_count, int) else None,
                title=title if isinstance(title, str) else None,
                authors=authors,
                subject=subject if isinstance(subject, str) else None,
                keywords=keywords,
                text_sample=text_sample,
                meta_json=meta_json,
            )

            cat_rules = categorize(
                cfg=cfg,
                source_path=src_path,
                source_basename=src_base,
                title=title if isinstance(title, str) else None,
                subject=subject if isinstance(subject, str) else None,
                keywords=keywords,
                authors=authors,
                text_sample=text_sample,
                page_count=page_count if isinstance(page_count, int) else None,
            )

            final_category = cat_rules.category
            final_score = cat_rules.score
            final_reason = cat_rules.reason

            if llm_provider != "off":
                should_call_llm = llm_mode == "always"
                if llm_mode == "fallback":
                    # Only call the LLM when our rule-based system can't place it confidently.
                    should_call_llm = (
                        final_category == default_category
                        or final_reason.startswith("below min_score")
                        or final_score < min_score
                    )

                if should_call_llm:
                    llm_calls += 1
                    try:
                        llm_result = llm_classify_category(
                            provider=llm_provider,  # type: ignore[arg-type]
                            model=str(llm_model),
                            categories=categories,
                            default_category=str(default_category),
                            source_path=src_path,
                            source_basename=src_base,
                            title=title if isinstance(title, str) else None,
                            authors=authors,
                            subject=subject if isinstance(subject, str) else None,
                            keywords=keywords,
                            page_count=page_count if isinstance(page_count, int) else None,
                            text_sample=text_sample if use_text else None,
                            timeout_seconds=float(llm_timeout_seconds),
                            max_output_tokens=int(llm_max_output_tokens),
                            path_mode=llm_path_mode,  # type: ignore[arg-type]
                            path_tail_parts=int(llm_path_tail_parts),
                        )

                        if llm_result.confidence >= float(llm_min_confidence):
                            final_category = llm_result.category
                            final_score = 10.0 * float(llm_result.confidence)
                            final_reason = (
                                f"llm:{llm_provider}/{llm_model} "
                                f"conf={llm_result.confidence:.2f}; {llm_result.reason}"
                            )
                            llm_used += 1
                        else:
                            # Keep the rules result; attach the low-confidence LLM hint for audit.
                            final_reason = (
                                f"{final_reason} | llm:{llm_provider}/{llm_model} "
                                f"low_conf={llm_result.confidence:.2f}"
                            )
                    except LLMError as e:
                        llm_failed += 1
                        final_reason = f"{final_reason} | llm_error:{str(e)[:200]}"

            db_mod.update_document_category(
                conn,
                hash_hex=hash_hex,
                category=final_category,
                score=float(final_score),
                reason=final_reason,
                categorized_at=now,
            )
            updated += 1

            if verbose and updated % 250 == 0:
                print(f"categorized {updated}…")

        # Always rebuild the categorized view from current DB state.
        all_docs = db_mod.iter_documents(conn)
        titles = {d["hash"]: (d.get("title") or "") for d in all_docs}

        def name_resolver(h: str) -> str:
            t = titles.get(h) or ""
            if t.strip():
                return t
            b = latest_source_basename_by_hash.get(h)
            if b:
                # strip trailing ".pdf" for nicer naming
                return b[:-4] if b.lower().endswith(".pdf") else b
            return h[:12]

        by_category = build_categorized_view(
            library=library,
            documents=all_docs,
            link_mode=link_mode,
            refresh=refresh_view,
            default_category=default_category,
            name_resolver=name_resolver,
        )

        conn.commit()

        return {
            "docs_categorized": updated,
            "links_created": by_category.get("_total_links", 0),
            "llm_calls": llm_calls,
            "llm_used": llm_used,
            "llm_failed": llm_failed,
            **{f"cat:{k}": v for k, v in by_category.items() if k != "_total_links"},
        }
    finally:
        conn.close()


