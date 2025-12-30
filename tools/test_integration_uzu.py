from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def main() -> None:
    """
    End-to-end test (no network):
      - create sample PDFs
      - scan -> vault
      - categorize using llm_provider=uzu with a monkeypatched HTTP client
    """
    repo = Path(__file__).resolve().parents[1]
    tmp = repo / ".tmp_integration"
    lib_root = tmp / "library_test"
    sample_docs = tmp / "sample_docs"

    if tmp.exists():
        shutil.rmtree(tmp)
    sample_docs.mkdir(parents=True)

    (sample_docs / "Invoice_2025.pdf").write_text("Invoice\nTotal Due: $123.45\n", encoding="utf-8")
    (sample_docs / "Widget3000_Manual.pdf").write_text(
        "User Guide\nInstallation instructions...\n", encoding="utf-8"
    )

    from pdf_lib.library import Library
    from pdf_lib.scanner import scan_and_copy
    from pdf_lib.catalog import categorize_library
    import pdf_lib.llm as llm_mod

    # Monkeypatch uzu HTTP call to return deterministic categories based on prompt content.
    def fake_http_post_json(*, url: str, headers: dict[str, str], payload: dict, timeout_seconds: float):
        assert url.endswith("/chat/completions"), url
        messages = payload.get("messages") or []
        prompt = messages[-1]["content"] if messages else ""
        p = str(prompt)

        # Avoid matching category names in the prompt; key off the explicit filename field.
        filename = ""
        for line in p.splitlines():
            if line.lower().startswith("- filename:"):
                filename = line.split(":", 1)[1].strip().lower()
                break

        if "invoice" in filename or "receipt" in filename:
            obj = {"category": "Receipts & Invoices", "confidence": 0.9, "reason": "invoice keyword"}
        elif "manual" in filename or "guide" in filename:
            obj = {"category": "Manuals & Guides", "confidence": 0.85, "reason": "manual keyword"}
        else:
            obj = {"category": "Unsorted", "confidence": 0.2, "reason": "no signal"}

        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": json.dumps(obj)},
                    "finish_reason": "stop",
                }
            ]
        }

    llm_mod._http_post_json = fake_http_post_json  # type: ignore[attr-defined]

    os.environ["UZU_BASE_URL"] = "http://localhost:8000"
    lib = Library(root=lib_root)

    scan_stats = scan_and_copy(
        library=lib,
        roots=[sample_docs],
        method="walk",
        exclude_prefixes=[],
        dry_run=False,
        limit=None,
        verbose=False,
    )
    assert scan_stats["copied_new"] == 2, scan_stats

    cat_stats = categorize_library(
        library=lib,
        config_path=None,
        link_mode="symlink",
        refresh_view=True,
        recategorize_all=True,
        text_sample_bytes=0,  # don't call mdls in the sandbox
        llm_provider="uzu",
        llm_model="qwen3-3b",
        llm_mode="always",
        llm_min_confidence=0.6,
        llm_timeout_seconds=2.0,
        llm_max_output_tokens=64,
        llm_path_mode="tail",
        llm_path_tail_parts=3,
        verbose=False,
    )

    assert cat_stats["llm_calls"] == 2, cat_stats
    assert cat_stats["llm_used"] == 2, cat_stats
    assert cat_stats["llm_failed"] == 0, cat_stats
    assert cat_stats.get("cat:Receipts & Invoices", 0) == 1, cat_stats
    assert cat_stats.get("cat:Manuals & Guides", 0) == 1, cat_stats

    # Check categorized view exists.
    assert (lib_root / "categorized").exists()
    assert any((lib_root / "categorized").rglob("*.pdf"))

    print("OK: integration scan -> uzu categorize -> categorized view works (no network).")


if __name__ == "__main__":
    main()


