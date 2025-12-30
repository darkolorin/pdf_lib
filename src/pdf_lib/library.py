from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import db as db_mod
from .util import ensure_dir


def default_library_path() -> Path:
    return Path.home() / "PDF_Library"


@dataclass(frozen=True)
class Library:
    root: Path

    @property
    def vault_dir(self) -> Path:
        return self.root / "vault"

    @property
    def categorized_dir(self) -> Path:
        return self.root / "categorized"

    @property
    def tmp_dir(self) -> Path:
        return self.root / ".pdf_lib_tmp"

    @property
    def db_path(self) -> Path:
        return self.root / "manifest.sqlite3"

    @property
    def categories_config_path(self) -> Path:
        return self.root / "categories.json"

    def ensure_initialized(self) -> None:
        ensure_dir(self.root)
        ensure_dir(self.vault_dir)
        ensure_dir(self.categorized_dir)
        ensure_dir(self.tmp_dir)

        if not self.categories_config_path.exists():
            # Copy packaged default config to the library so the user can edit it.
            from importlib import resources

            default_bytes = resources.files("pdf_lib").joinpath("default_categories.json").read_bytes()
            self.categories_config_path.write_bytes(default_bytes)

        conn = db_mod.connect(self.db_path)
        try:
            db_mod.init_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def vault_path_for_hash(self, sha256_hex: str) -> Path:
        # Fan out to avoid huge single directories.
        prefix = Path(sha256_hex[:2]) / sha256_hex[2:4]
        return self.vault_dir / prefix / f"{sha256_hex}.pdf"

    def load_categories_config(self, path: Path | None = None) -> dict:
        cfg_path = path or self.categories_config_path
        return json.loads(cfg_path.read_text(encoding="utf-8"))


