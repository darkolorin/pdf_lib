from __future__ import annotations

import os
import shutil
from pathlib import Path

from .library import Library
from .util import ensure_dir, safe_filename


def _relative_symlink(target: Path, link_path: Path) -> None:
    ensure_dir(link_path.parent)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink(missing_ok=True)
    rel_target = os.path.relpath(str(target), start=str(link_path.parent))
    link_path.symlink_to(rel_target)


def _hardlink(target: Path, link_path: Path) -> None:
    ensure_dir(link_path.parent)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink(missing_ok=True)
    os.link(target, link_path)


def _copy(target: Path, link_path: Path) -> None:
    ensure_dir(link_path.parent)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink(missing_ok=True)
    shutil.copy2(target, link_path)


def build_categorized_view(
    *,
    library: Library,
    documents: list[dict],
    link_mode: str = "symlink",
    refresh: bool = True,
    default_category: str = "Unsorted",
    name_resolver,
) -> dict[str, int]:
    """
    Create `categorized/<Category>/...pdf` entries pointing at the vault.

    name_resolver(hash_hex) -> display_base_name (without extension)
    """
    categorized = library.categorized_dir
    ensure_dir(categorized)

    if refresh:
        # The categorized view is derived; safe to rebuild from scratch.
        for child in categorized.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)

    linker = {
        "symlink": _relative_symlink,
        "hardlink": _hardlink,
        "copy": _copy,
    }.get(link_mode)
    if linker is None:
        raise ValueError(f"Unknown link_mode={link_mode!r}")

    created = 0
    by_category: dict[str, int] = {}

    for doc in documents:
        hash_hex = doc["hash"]
        vault_relpath = doc["vault_relpath"]
        category = (doc.get("category") or default_category).strip() or default_category

        cat_dir = categorized / safe_filename(category)
        ensure_dir(cat_dir)

        base_name = safe_filename(name_resolver(hash_hex))
        if not base_name.lower().endswith(".pdf"):
            base_name = base_name + ".pdf"

        # Avoid collisions deterministically.
        stem = base_name[:-4] if base_name.lower().endswith(".pdf") else base_name
        candidate = cat_dir / f"{stem}__{hash_hex[:8]}.pdf"
        n = 2
        while candidate.exists() or candidate.is_symlink():
            candidate = cat_dir / f"{stem}__{hash_hex[:8]}__{n}.pdf"
            n += 1

        vault_path = library.root / vault_relpath
        try:
            linker(vault_path, candidate)
            created += 1
            by_category[category] = by_category.get(category, 0) + 1
        except OSError:
            # Hardlinks can fail across filesystems; fall back to symlink.
            if link_mode == "hardlink":
                _relative_symlink(vault_path, candidate)
                created += 1
                by_category[category] = by_category.get(category, 0) + 1
            else:
                raise

    by_category["_total_links"] = created
    return by_category


