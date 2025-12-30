### pdf-lib (macOS PDF collector + categorizer)

This repo gives you a **repeatable, incremental** way to:

- **Find PDFs** on your Mac (fast via Spotlight `mdfind`, with a filesystem-walk fallback)
- **Copy** (never move) them into a managed **deduped vault**
- Create a **categorized folder view** (symlinks by default) you can drag into **Apple Books**

It stores state in a SQLite manifest so you can re-run it anytime and it will only copy what changed.

Docs:
- `GUIDE.md` (full walkthrough + architecture)

---

### Quick start

From this repo:

```bash
# 1) Install uv if you don't have it (recommended)
# brew install uv
#
# 2) Run the repo wrapper (no pip install needed)
chmod +x ./pdf-lib
./pdf-lib init --library ~/PDF_Library
./pdf-lib run --library ~/PDF_Library

# Optional: create a persistent project venv with uv (recommended if you edit code)
uv sync
uv run -- pdf-lib --help
```

If you prefer using uv directly (instead of the wrapper):

```bash
uv sync
uv run -- pdf-lib init --library ~/PDF_Library
uv run -- pdf-lib run --library ~/PDF_Library
```

Then open Finder to `~/PDF_Library/categorized/` and drag category folders (or individual PDFs) into Apple Books.

---

### Recommended macOS permissions

If you want to scan everything under your home folder (and especially things like Desktop/Documents/Downloads/iCloud Drive), your terminal/Python may need **Full Disk Access**:

- System Settings → Privacy & Security → Full Disk Access → enable for your Terminal app (and/or Cursor, if you run it there)

If you see “permission denied” errors, that’s almost always the fix.

---

### Commands

All examples below use the repo wrapper `./pdf-lib ...` (it will automatically use `uv run` if uv is installed).

#### Initialize a library

```bash
./pdf-lib init --library ~/PDF_Library
```

Creates:

- `vault/` (deduped copies, named by SHA-256 hash)
- `categorized/` (category folders that point at the vault)
- `manifest.sqlite3` (state so rescans are incremental)
- `categories.json` (rules you can edit)

#### Scan + copy into the vault (does NOT move originals)

```bash
./pdf-lib scan --library ~/PDF_Library
```

By default it uses Spotlight (`mdfind`) scoped to sensible roots. You can override:

```bash
./pdf-lib scan --library ~/PDF_Library --roots ~ /Volumes --method mdfind
./pdf-lib scan --library ~/PDF_Library --roots ~ --method walk
```

#### Categorize and build the categorized view

```bash
./pdf-lib categorize --library ~/PDF_Library
```

Link modes:

- `symlink` (default): minimal disk use; usually works fine for Apple Books imports
- `hardlink`: no extra disk use, but only works within the same filesystem
- `copy`: duplicates files into category folders (most compatible, uses more disk)

```bash
./pdf-lib categorize --library ~/PDF_Library --link-mode symlink
./pdf-lib categorize --library ~/PDF_Library --link-mode copy
```

#### One-shot run (scan + categorize)

```bash
./pdf-lib run --library ~/PDF_Library
```

---

### LLM-assisted categorization (optional, local-only)

By default, categorization is **local + rule-based** (no network). You can optionally let a **local** LLM (UZU) help classify “Unsorted” PDFs.

#### Use local UZU (OpenAI-compatible server)

UZU runs a **local OpenAI-compatible server** on your Mac (Apple Silicon).

1) Set up UZU and a local model (SafeTensors format).

2) Start the server (example; adjust paths for your machine):

```bash
cd /path/to/uzu
cargo run --release -p cli -- serve /path/to/uzu/models/<engine-version>/<model-name>
```

3) Run categorization using the local provider:

```bash
export UZU_BASE_URL="http://localhost:8000"
./pdf-lib categorize --library ~/PDF_Library --llm-provider uzu
```

#### Cost + privacy controls

- **Only call the LLM when needed** (default): `--llm-mode fallback`
- **Limit text sent**: `--text-sample-bytes 2048` (or `0` to send no text)
- **Limit path sent**: `--llm-path-mode basename` (or `tail`/`full`)

---

### Customize categories

Edit `~/PDF_Library/categories.json` and re-run:

```bash
./pdf-lib categorize --library ~/PDF_Library --all
```

Every categorized document stores a reason/score in the manifest so you can audit what happened.


