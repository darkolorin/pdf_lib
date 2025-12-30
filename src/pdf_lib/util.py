from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable


def now_ts() -> float:
    return time.time()


def expand_path(p: str | Path) -> Path:
    if isinstance(p, Path):
        p2 = p
    else:
        p2 = Path(p)
    return p2.expanduser()


def resolve_path(p: Path) -> Path:
    # strict=False avoids exceptions for paths that don't exist (or are transient).
    return p.expanduser().resolve(strict=False)


def is_under(path: Path, prefix: Path) -> bool:
    path_r = resolve_path(path)
    prefix_r = resolve_path(prefix)
    return path_r == prefix_r or path_r.is_relative_to(prefix_r)


def dedupe_keep_order(items: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in items:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_filename(name: str, *, max_len: int = 160) -> str:
    name = name.strip().replace("\u0000", "")
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "untitled"
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def ensure_parent_dir(path: Path) -> None:
    ensure_dir(path.parent)


def is_probably_pdf_path(path: Path) -> bool:
    # Spotlight can return PDFs without a .pdf extension; we accept those too.
    # For filesystem-walk scanning we mostly rely on the suffix.
    return path.suffix.lower() == ".pdf"


@dataclass(frozen=True)
class CopyResult:
    sha256_hex: str
    bytes_written: int
    tmp_path: Path


def copy_to_temp_and_hash(src: Path, tmp_dir: Path, *, chunk_size: int = 1024 * 1024) -> CopyResult:
    ensure_dir(tmp_dir)
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}.tmp"

    h = sha256()
    bytes_written = 0

    with src.open("rb") as rf, tmp_path.open("wb") as wf:
        while True:
            chunk = rf.read(chunk_size)
            if not chunk:
                break
            wf.write(chunk)
            h.update(chunk)
            bytes_written += len(chunk)

        wf.flush()
        os.fsync(wf.fileno())

    return CopyResult(sha256_hex=h.hexdigest(), bytes_written=bytes_written, tmp_path=tmp_path)


def atomic_move(src: Path, dest: Path) -> None:
    ensure_parent_dir(dest)
    src.replace(dest)


def remove_tree_contents(dir_path: Path) -> None:
    if not dir_path.exists():
        return
    for child in dir_path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def read_text_limited(cmd: list[str], *, limit_bytes: int) -> str:
    """
    Run a command and read up to limit_bytes from stdout, then terminate.
    Intended for large producers (e.g. mdls text content).
    """
    import subprocess

    if limit_bytes <= 0:
        return ""

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None

    chunks: list[bytes] = []
    remaining = limit_bytes
    try:
        while remaining > 0:
            data = proc.stdout.read(min(4096, remaining))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except Exception:
            proc.kill()
    return b"".join(chunks).decode("utf-8", errors="ignore")


