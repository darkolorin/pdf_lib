from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Categorization:
    category: str
    score: float
    reason: str


def _lower(s: str | None) -> str:
    return (s or "").lower()


def _any_kw(haystack: str, keywords: Iterable[str]) -> list[str]:
    hs = haystack.lower()
    hits: list[str] = []
    for kw in keywords:
        kw_l = kw.lower().strip()
        if not kw_l:
            continue
        if kw_l in hs:
            hits.append(kw)
    return hits


def categorize(
    *,
    cfg: dict[str, Any],
    source_path: str | None,
    source_basename: str | None,
    title: str | None,
    subject: str | None,
    keywords: str | None,
    authors: str | None,
    text_sample: str | None,
    page_count: int | None,
) -> Categorization:
    default_category = cfg.get("default_category", "Unsorted")
    min_score = float(cfg.get("min_score", 4))

    path_l = _lower(source_path)
    base_l = _lower(source_basename)
    meta_l = " ".join([_lower(title), _lower(subject), _lower(keywords), _lower(authors)])
    text_l = _lower(text_sample)

    best = Categorization(category=default_category, score=0.0, reason="no rules matched")

    for cat in cfg.get("categories", []):
        name = str(cat.get("name", "")).strip()
        if not name:
            continue

        min_pages = cat.get("min_pages")
        max_pages = cat.get("max_pages")
        if page_count is not None:
            if isinstance(min_pages, int) and page_count < min_pages:
                continue
            if isinstance(max_pages, int) and page_count > max_pages:
                continue

        score = 0.0
        reasons: list[str] = []

        path_hits = _any_kw(path_l, cat.get("path_keywords_any", []))
        if path_hits:
            score += 2.0 + 0.25 * (len(path_hits) - 1)
            reasons.append(f"path:{path_hits[0]}")

        file_hits = _any_kw(base_l, cat.get("filename_keywords_any", []))
        if file_hits:
            score += 2.0 + 0.25 * (len(file_hits) - 1)
            reasons.append(f"filename:{file_hits[0]}")

        meta_hits = _any_kw(meta_l, cat.get("metadata_keywords_any", []))
        if meta_hits:
            score += 3.0 + 0.25 * (len(meta_hits) - 1)
            reasons.append(f"meta:{meta_hits[0]}")

        text_hits = _any_kw(text_l, cat.get("text_keywords_any", []))
        if text_hits:
            score += 4.0 + 0.25 * (len(text_hits) - 1)
            reasons.append(f"text:{text_hits[0]}")

        # Tie-breaker / preference
        priority = cat.get("priority", 0)
        score += float(priority) * 1e-6

        if score > best.score:
            best = Categorization(category=name, score=score, reason=", ".join(reasons) or "matched category")

    if best.score < min_score:
        return Categorization(category=default_category, score=best.score, reason="below min_score; defaulted")
    return best


def config_uses_text(cfg: dict[str, Any]) -> bool:
    for cat in cfg.get("categories", []):
        kws = cat.get("text_keywords_any", [])
        if isinstance(kws, list) and any(str(x).strip() for x in kws):
            return True
    return False


