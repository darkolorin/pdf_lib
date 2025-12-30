### pdf-lib: full guide

This guide explains what `pdf-lib` does, how the library layout works, how rescans are incremental, and how (optional) local-LLM categorization works.

---

### What it does (high level)

`pdf-lib` is a **local PDF collector**:

- **Discovers PDFs** on macOS (fast via Spotlight `mdfind`, fallback via filesystem walk)
- **Copies** them (never moves originals) into a deduped **vault**
- Builds a **categorized view** (folders per category) suitable for importing into Apple Books
- Stores state in a **SQLite manifest** so you can re-run it anytime and it will only do work for changes

---

### Repository layout

- `pdf-lib`: repo wrapper script (sets `PYTHONPATH=src` and runs `python -m pdf_lib ...`)
- `src/pdf_lib/cli.py`: CLI commands (`init`, `scan`, `categorize`, `run`)
- `src/pdf_lib/scanner.py`: PDF discovery + copy into vault + incremental logic
- `src/pdf_lib/db.py`: SQLite schema + queries
- `src/pdf_lib/catalog.py`: metadata capture + categorization + categorized-view rebuild
- `src/pdf_lib/categorizer.py`: rule-based categorization engine
- `src/pdf_lib/llm.py`: **local-only** LLM categorization provider (`uzu`)
- `src/pdf_lib/organize.py`: builds `categorized/` view (symlink/hardlink/copy)
- `src/pdf_lib/metadata.py`: Spotlight metadata (`mdls`) + optional text sample
- `src/pdf_lib/library.py`: library folder conventions
- `src/pdf_lib/default_categories.json`: default category rules copied into your library as `categories.json`
- `tools/`: local tests

---

### Library folder layout (what gets created)

When you run:

```bash
./pdf-lib init --library ~/PDF_Library
```

You get:

- `~/PDF_Library/vault/`
  - Deduped PDF copies, named by **SHA-256** of file content.
  - Stored as: `vault/aa/bb/<sha256>.pdf` (fan-out avoids huge directories).
- `~/PDF_Library/categorized/`
  - A derived “view” rebuilt from the manifest.
  - Contains category folders; files are **symlinks** by default (can also hardlink/copy).
- `~/PDF_Library/manifest.sqlite3`
  - The manifest DB (what was seen, what hash it maps to, category decisions, etc).
- `~/PDF_Library/categories.json`
  - Editable rules defining your categories.
- `~/PDF_Library/.pdf_lib_tmp/`
  - Temporary files used for atomic copy + hashing.

---

### How scanning works (and why rescans are fast)

Discovery:
- **Default**: Spotlight `mdfind` for speed.
- Fallback: filesystem walk for environments where Spotlight is unavailable.

Incremental behavior:
- For each source path, `pdf-lib` records **size + mtime** in `source_files`.
- On rescan, if size+mtime match a previously ingested file, it’s counted as `skipped_unchanged`
  and **not rehashed**.

Copy + dedupe:
- New/changed source files are streamed to a temp file while computing SHA-256.
- The final vault path is determined from the hash.
- If the vault file already exists, the copy is **deduped** (no duplicate vault storage).

Safety:
- Originals are never moved or deleted.
- Only writes happen inside `--library` (plus any temp directory inside it).

---

### How categorization works

There are two layers:

#### 1) Rule-based (default, fully offline)

Rules live in your library’s `categories.json`. Each category can match:
- keywords in **source path**
- keywords in **filename**
- keywords in **Spotlight metadata** (title/subject/keywords/authors)
- keywords in **Spotlight extracted text sample** (if enabled)

If no category scores above `min_score`, the PDF goes into `default_category` (usually `Unsorted`).

#### 2) Optional local LLM (UZU) for tougher cases

Enable with:

```bash
export UZU_BASE_URL="http://localhost:8000"
./pdf-lib categorize --library ~/PDF_Library --llm-provider uzu
```

Modes:
- `--llm-mode fallback` (default): only call LLM when rule-based is “not confident”
  (e.g. defaulted to `Unsorted` / below `min_score`).
- `--llm-mode always`: call LLM for every document.

Confidence:
- LLM must return JSON with: `category`, `confidence` (0..1), `reason`.
- If `confidence >= --llm-min-confidence`, LLM can override the rule-based category.

Privacy controls (what gets sent to the LLM):
- `--text-sample-bytes N`: max bytes of Spotlight extracted text sent (0 disables text)
- `--llm-path-mode basename|tail|full`: send only filename, last N path parts, or full path

Note: the LLM must be **local**; this repo does not include any cloud providers.

---

### Apple Books import workflow

Use the categorized view:

- Open Finder → `~/PDF_Library/categorized/`
- Drag category folders (or individual PDFs) into Apple Books

If Apple Books doesn’t like symlinks on your setup:

```bash
./pdf-lib categorize --library ~/PDF_Library --link-mode copy --all
```

---

### Troubleshooting

- **Permission denied / missing files**
  - macOS may block access without Full Disk Access.
  - System Settings → Privacy & Security → Full Disk Access → enable for your Terminal/Cursor.

- **Spotlight returns fewer PDFs than expected**
  - Some locations may not be indexed yet.
  - Use `--method walk` to force filesystem scanning (slower).

- **UZU server outputs “not-quite JSON”**
  - `pdf-lib` is tolerant (it tries JSON, python-dict, and regex extraction).
  - Best results come from instructing the model to return *only* JSON.

---

### Recommended production run pattern

Start small:

```bash
./pdf-lib run --library ~/PDF_Library --roots ~/Downloads --limit 100
```

Then scale up:

```bash
export UZU_BASE_URL="http://localhost:8000"
./pdf-lib run --library ~/PDF_Library --roots ~ /Volumes --method mdfind --llm-provider uzu
```


