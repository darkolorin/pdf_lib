from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from .util import read_text_limited


_MDLS = "/usr/bin/mdls"


def _clean_mdls_value(val: Any) -> Any:
    # mdls sometimes returns "(null)" as a string sentinel.
    if val is None:
        return None
    if isinstance(val, str) and val.strip() == "(null)":
        return None
    return val


def mdls_basic(path: Path) -> dict[str, Any]:
    """
    Fetch small-ish metadata via `mdls -plist` (fast, structured).
    """
    names = [
        "kMDItemTitle",
        "kMDItemAuthors",
        "kMDItemSubject",
        "kMDItemKeywords",
        "kMDItemNumberOfPages",
        "kMDItemContentType",
        "kMDItemContentTypeTree",
    ]
    cmd = [_MDLS, "-plist"]
    for name in names:
        cmd += ["-name", name]
    cmd.append(str(path))

    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not res.stdout:
        return {}
    try:
        plist = plistlib.loads(res.stdout)
    except Exception:
        return {}

    out: dict[str, Any] = {}
    for k, v in plist.items():
        out[k] = _clean_mdls_value(v)
    return out


def mdls_text_sample(path: Path, *, max_bytes: int = 8192) -> str | None:
    """
    Get a limited text sample from Spotlight's extracted text.
    This avoids third-party PDF text extraction deps and avoids reading huge outputs.
    """
    if max_bytes <= 0:
        return None
    cmd = [_MDLS, "-raw", "-name", "kMDItemTextContent", str(path)]
    txt = read_text_limited(cmd, limit_bytes=max_bytes)
    txt = txt.strip()
    if not txt or txt == "(null)":
        return None
    return txt


def mdls_meta_json(path: Path) -> str | None:
    """
    A compact JSON blob of mdls basic metadata for later audit/debugging.
    """
    data = mdls_basic(path)
    if not data:
        return None
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    except Exception:
        return None


