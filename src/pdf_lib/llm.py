from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from ast import literal_eval
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


ProviderName = Literal["uzu"]
PathMode = Literal["basename", "tail", "full"]


@dataclass(frozen=True)
class LLMClassification:
    category: str
    confidence: float
    reason: str
    raw_text: str


class LLMError(RuntimeError):
    pass


def _http_post_json(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        raise LLMError(f"HTTP {e.code} from {url}: {body[:800]!r}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Network error calling {url}: {e}") from e

    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        raise LLMError(f"Non-JSON response from {url}: {body[:800]!r}") from e


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _extract_json_object(text: str) -> dict[str, Any]:
    """
    Try hard to recover a JSON object from model output.
    """
    t = text.strip()
    if not t:
        raise LLMError("Empty model output")

    # Remove common markdown fences
    t2 = _CODE_FENCE_RE.sub("", t).strip()
    # Normalize smart quotes that frequently appear in model outputs.
    t2 = (
        t2.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )

    # If it is already a JSON object, parse directly.
    if t2.startswith("{") and t2.endswith("}"):
        try:
            obj = json.loads(t2)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # Robust mode: scan for JSON objects using raw_decode from every '{'
    # and pick the "best" one (prefer objects that contain a string "category").
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for i, ch in enumerate(t2):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(t2[i:])
        except Exception:
            continue
        if isinstance(obj, dict):
            candidates.append(obj)

    for obj in candidates:
        if isinstance(obj.get("category"), str):
            return obj
    if candidates:
        return candidates[0]

    # Fallback: find the largest {...} span.
    start = t2.find("{")
    end = t2.rfind("}")
    if start == -1 or end == -1 or end <= start:
        # Last-ditch: try regex extraction even if braces are missing.
        return _extract_object_by_regex(t2)

    candidate = t2[start : end + 1]
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
        raise LLMError("Model output JSON was not an object")
    except Exception as e:
        # Many local models output "Python dicts" with single quotes.
        try:
            obj2 = literal_eval(candidate)
            if isinstance(obj2, dict):
                return {str(k): v for k, v in obj2.items()}
        except Exception:
            pass

        # Final attempt: regex extraction from the (cleaned) text.
        return _extract_object_by_regex(t2)


def _extract_object_by_regex(text: str) -> dict[str, Any]:
    """
    Heuristic extraction for non-JSON-ish outputs, e.g.
      {'category': 'X', 'confidence': 0.9, 'reason': '...'}
      category: "X", confidence: 0.9, reason: "..."
    """
    out: dict[str, Any] = {}
    # Category
    m = re.search(r"category\s*[:=]\s*['\"]([^'\"]+)['\"]", text, flags=re.IGNORECASE)
    if m:
        out["category"] = m.group(1).strip()

    # Confidence
    m = re.search(r"confidence\s*[:=]\s*([0-9]*\.?[0-9]+)", text, flags=re.IGNORECASE)
    if m:
        out["confidence"] = m.group(1)

    # Reason
    m = re.search(r"reason\s*[:=]\s*['\"]([^'\"]+)['\"]", text, flags=re.IGNORECASE)
    if m:
        out["reason"] = m.group(1).strip()

    return out


def _normalize_category_name(name: str) -> str:
    # Lower + remove punctuation-ish to tolerate minor formatting differences.
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _format_source_path(path_str: str | None, *, mode: PathMode, tail_parts: int) -> str | None:
    if not path_str:
        return None
    p = Path(path_str)
    if mode == "basename":
        return p.name

    if mode == "full":
        # Replace home with "~" (avoids leaking username).
        try:
            home = str(Path.home())
            s = str(p)
            if s.startswith(home + os.sep):
                return "~" + s[len(home) :]
            return s
        except Exception:
            return str(p)

    # mode == "tail"
    parts = p.parts
    # Replace the absolute prefix with "…"
    tail = parts[-max(1, tail_parts) :]
    return "…/" + "/".join(tail)


def build_categorization_prompt(
    *,
    categories: list[str],
    default_category: str,
    source_path: str | None,
    source_basename: str | None,
    title: str | None,
    authors: str | None,
    subject: str | None,
    keywords: str | None,
    page_count: int | None,
    text_sample: str | None,
) -> str:
    cats_json = json.dumps(categories, ensure_ascii=False)
    lines: list[str] = []
    lines.append("You are a precise document librarian.")
    lines.append("Pick the single best category for this PDF from the allowed list.")
    lines.append("Return ONLY valid JSON (no markdown) with keys: category, confidence, reason.")
    lines.append(f"- category must be exactly one of: {cats_json}")
    lines.append("- confidence must be a number between 0 and 1.")
    lines.append("- reason must be short (<= 140 chars).")
    lines.append(f"If unsure, use category={json.dumps(default_category)} with low confidence.")
    lines.append("")
    lines.append("PDF info:")
    if source_path:
        lines.append(f"- source_path: {source_path}")
    if source_basename:
        lines.append(f"- filename: {source_basename}")
    if title:
        lines.append(f"- title: {title}")
    if authors:
        lines.append(f"- authors: {authors}")
    if subject:
        lines.append(f"- subject: {subject}")
    if keywords:
        lines.append(f"- keywords: {keywords}")
    if page_count is not None:
        lines.append(f"- pages: {page_count}")
    if text_sample:
        # Keep the prompt stable: strip excessive whitespace.
        sample = re.sub(r"\s+", " ", text_sample).strip()
        lines.append(f"- text_sample: {sample}")
    return "\n".join(lines).strip() + "\n"


def classify_with_uzu(
    *,
    base_url: str,
    model: str | None,
    prompt: str,
    timeout_seconds: float,
    max_output_tokens: int,
) -> str:
    """
    UZU local server (OpenAI-compatible). See your local UZU docs (AGENT_GUIDE.md).

    Endpoint: POST {base_url}/chat/completions
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("Missing UZU_BASE_URL (e.g. http://localhost:8000)")

    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        # UZU guide uses OpenAI-style `max_completion_tokens`
        "max_completion_tokens": int(max_output_tokens),
        "temperature": 0,
        "stream": False,
    }
    if model:
        payload["model"] = model

    resp = _http_post_json(url=url, headers=headers, payload=payload, timeout_seconds=timeout_seconds)

    # OpenAI chat.completions-like
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else None
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]

            # Some servers may respond with a simple "text" field
            if isinstance(first.get("text"), str):
                return first["text"]

    # Some servers return a top-level "output_text"
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]

    raise LLMError(f"Unexpected UZU response shape: keys={sorted(resp.keys())}")


def llm_classify_category(
    *,
    provider: ProviderName,
    model: str,
    categories: list[str],
    default_category: str,
    source_path: str | None,
    source_basename: str | None,
    title: str | None,
    authors: str | None,
    subject: str | None,
    keywords: str | None,
    page_count: int | None,
    text_sample: str | None,
    timeout_seconds: float = 30.0,
    max_output_tokens: int = 200,
    path_mode: PathMode = "tail",
    path_tail_parts: int = 3,
) -> LLMClassification:
    if provider != "uzu":
        raise LLMError("Only the local UZU provider is supported.")

    # Build category map for normalization.
    allowed = list(dict.fromkeys([*categories, default_category]))
    normalized = {_normalize_category_name(c): c for c in allowed}

    prompt = build_categorization_prompt(
        categories=allowed,
        default_category=default_category,
        source_path=_format_source_path(source_path, mode=path_mode, tail_parts=path_tail_parts),
        source_basename=source_basename,
        title=title,
        authors=authors,
        subject=subject,
        keywords=keywords,
        page_count=page_count,
        text_sample=text_sample,
    )

    started = time.time()
    base_url = os.environ.get("UZU_BASE_URL", "http://localhost:8000").strip()
    raw_text = classify_with_uzu(
        base_url=base_url,
        model=model if model else None,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )

    parsed = _extract_json_object(raw_text)
    cat_raw = parsed.get("category")
    conf_raw = parsed.get("confidence")
    reason_raw = parsed.get("reason")

    if not isinstance(cat_raw, str):
        # Treat malformed outputs as low-confidence defaults (do not fail the run).
        return LLMClassification(
            category=default_category,
            confidence=0.0,
            reason="missing/invalid category; defaulted",
            raw_text=raw_text,
        )

    cat_norm = _normalize_category_name(cat_raw)
    cat = normalized.get(cat_norm, default_category)

    try:
        conf = float(conf_raw)
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    reason = str(reason_raw or "").strip()
    if len(reason) > 200:
        reason = reason[:200].rstrip()

    elapsed = time.time() - started
    reason = reason or f"llm classified in {elapsed:.2f}s"

    return LLMClassification(category=cat, confidence=conf, reason=reason, raw_text=raw_text)


