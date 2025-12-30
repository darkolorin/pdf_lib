from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import db as db_mod
from .library import Library
from .util import CopyResult, copy_to_temp_and_hash, ensure_dir, is_under, now_ts, resolve_path


def default_scan_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Library" / "Mobile Documents" / "com~apple~CloudDocs",  # iCloud Drive
        home / "Library" / "CloudStorage",  # Dropbox/OneDrive/etc (if present)
        home,
    ]
    # Keep only existing roots (except home, which always exists)
    out: list[Path] = []
    for r in roots:
        if r == home or r.exists():
            out.append(r)
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[Path] = []
    for r in out:
        s = str(r)
        if s in seen:
            continue
        seen.add(s)
        deduped.append(r)
    return deduped


def default_excludes() -> list[Path]:
    home = Path.home()
    return [
        home / ".Trash",
        home / ".cache",
        home / "Library" / "Caches",
        home / "Library" / "Containers",
        home / "Library" / "Group Containers",
        home / "Library" / "Logs",
        home / "Library" / "Mail",
        home / "Library" / "Safari",
        home / "Library" / "Developer",
        Path("/System"),
        Path("/private"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
    ]


def _is_excluded(path: Path, exclude_prefixes: list[Path]) -> bool:
    for ex in exclude_prefixes:
        if is_under(path, ex):
            return True
    return False


def find_pdfs_mdfind(roots: list[Path], *, exclude_prefixes: list[Path], limit: int | None = None):
    """
    Use Spotlight for fast search.
    """
    query = (
        '(kMDItemContentType == "com.adobe.pdf" || kMDItemContentTypeTree == "com.adobe.pdf" '
        '|| kMDItemFSName == "*.pdf")'
    )

    yielded = 0
    seen: set[str] = set()
    for root in roots:
        root = resolve_path(root)
        cmd = ["/usr/bin/mdfind", "-onlyin", str(root), query]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            p = line.strip()
            if not p:
                continue
            if p in seen:
                continue
            seen.add(p)
            path = Path(p)
            if _is_excluded(path, exclude_prefixes):
                continue
            if not path.exists() or not path.is_file():
                continue
            yield path
            yielded += 1
            if limit is not None and yielded >= limit:
                proc.terminate()
                return
        proc.wait()


def find_pdfs_walk(roots: list[Path], *, exclude_prefixes: list[Path], limit: int | None = None):
    """
    Slower fallback: walk the filesystem and pick *.pdf (case-insensitive).
    """
    yielded = 0
    seen: set[str] = set()
    for root in roots:
        root = resolve_path(root)
        if not root.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            dir_path = Path(dirpath)

            # Prune excluded dirs in-place for speed.
            pruned: list[str] = []
            for d in list(dirnames):
                dp = dir_path / d
                if _is_excluded(dp, exclude_prefixes):
                    pruned.append(d)
            for d in pruned:
                dirnames.remove(d)

            for fn in filenames:
                if not fn.lower().endswith(".pdf"):
                    continue
                full = dir_path / fn
                key = str(full)
                if key in seen:
                    continue
                seen.add(key)
                if _is_excluded(full, exclude_prefixes):
                    continue
                try:
                    if not full.is_file():
                        continue
                except OSError:
                    continue
                yield full
                yielded += 1
                if limit is not None and yielded >= limit:
                    return


def _copy_into_vault(library: Library, src: Path) -> tuple[str, Path, int, bool]:
    """
    Returns (hash_hex, vault_path, bytes_written, was_copied)
    """
    ensure_dir(library.tmp_dir)
    result: CopyResult = copy_to_temp_and_hash(src, library.tmp_dir)
    vault_path = library.vault_path_for_hash(result.sha256_hex)

    if vault_path.exists():
        result.tmp_path.unlink(missing_ok=True)
        return result.sha256_hex, vault_path, result.bytes_written, False

    vault_path.parent.mkdir(parents=True, exist_ok=True)
    result.tmp_path.replace(vault_path)
    try:
        shutil.copystat(src, vault_path, follow_symlinks=True)
    except OSError:
        # Not critical; the copy itself succeeded.
        pass
    return result.sha256_hex, vault_path, result.bytes_written, True


def scan_and_copy(
    *,
    library: Library,
    roots: list[Path],
    method: str = "auto",
    exclude_prefixes: list[Path] | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    verbose: bool = False,
) -> dict[str, int]:
    exclude_prefixes = exclude_prefixes or default_excludes()

    method = method.lower().strip()
    if method not in {"auto", "mdfind", "walk"}:
        raise ValueError("method must be one of: auto, mdfind, walk")

    use_mdfind = method in {"auto", "mdfind"}
    if use_mdfind and shutil.which("mdfind") is None:
        use_mdfind = False

    finder = find_pdfs_mdfind if use_mdfind else find_pdfs_walk

    if dry_run:
        stats = {
            "discovered": 0,
            "skipped_unchanged": 0,
            "copied_new": 0,
            "deduped_existing": 0,
            "errors": 0,
        }
        for src in finder(roots, exclude_prefixes=exclude_prefixes, limit=limit):
            stats["discovered"] += 1
            if verbose:
                print(f"[dry-run] would copy: {src}")
        return stats

    library.ensure_initialized()
    conn = db_mod.connect(library.db_path)
    try:
        db_mod.init_schema(conn)
        now = now_ts()

        stats = {
            "discovered": 0,
            "skipped_unchanged": 0,
            "copied_new": 0,
            "deduped_existing": 0,
            "errors": 0,
        }

        for src in finder(roots, exclude_prefixes=exclude_prefixes, limit=limit):
            stats["discovered"] += 1
            src_str = str(src)
            base = src.name

            try:
                st = src.stat()
            except (OSError, PermissionError) as e:
                stats["errors"] += 1
                db_mod.upsert_source(
                    conn,
                    source_path=src_str,
                    source_basename=base,
                    source_size=None,
                    source_mtime=None,
                    hash_hex=None,
                    status="unreadable",
                    error=str(e),
                    first_seen_at=now,
                    last_seen_at=now,
                )
                continue

            rec = db_mod.get_source(conn, src_str)
            if rec and rec.get("status") == "ok":
                if rec.get("source_size") == st.st_size and rec.get("source_mtime") == st.st_mtime:
                    stats["skipped_unchanged"] += 1
                    db_mod.touch_source_seen(conn, src_str, seen_at=now)
                    continue

            if dry_run:
                if verbose:
                    print(f"[dry-run] would copy: {src}")
                continue

            try:
                hash_hex, vault_path, bytes_written, was_copied = _copy_into_vault(library, src)
            except (OSError, PermissionError) as e:
                stats["errors"] += 1
                db_mod.upsert_source(
                    conn,
                    source_path=src_str,
                    source_basename=base,
                    source_size=st.st_size,
                    source_mtime=st.st_mtime,
                    hash_hex=None,
                    status="error",
                    error=str(e),
                    first_seen_at=now,
                    last_seen_at=now,
                )
                continue

            vault_relpath = str(vault_path.relative_to(library.root))
            db_mod.upsert_document_seen(
                conn,
                hash_hex=hash_hex,
                vault_relpath=vault_relpath,
                file_size=bytes_written,
                first_seen_at=now,
                last_seen_at=now,
            )

            db_mod.upsert_source(
                conn,
                source_path=src_str,
                source_basename=base,
                source_size=st.st_size,
                source_mtime=st.st_mtime,
                hash_hex=hash_hex,
                status="ok",
                error=None,
                first_seen_at=now,
                last_seen_at=now,
            )

            if was_copied:
                stats["copied_new"] += 1
            else:
                stats["deduped_existing"] += 1

        conn.commit()
        return stats
    finally:
        conn.close()


