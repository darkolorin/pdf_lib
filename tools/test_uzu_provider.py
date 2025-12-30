from __future__ import annotations

import os


def main() -> None:
    # Run from repo root with: PYTHONPATH=src python3 tools/test_uzu_provider.py
    from pdf_lib import llm

    # Monkeypatch network call so we don't need a running server.
    def fake_http_post_json(*, url: str, headers: dict[str, str], payload: dict, timeout_seconds: float):
        assert url.endswith("/chat/completions"), url
        assert payload.get("messages"), payload
        # Return an OpenAI-compatible response with JSON content.
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"category":"Receipts & Invoices","confidence":0.85,"reason":"invoice keyword"}',
                    },
                    "finish_reason": "stop",
                }
            ]
        }

    llm._http_post_json = fake_http_post_json  # type: ignore[attr-defined]
    os.environ["UZU_BASE_URL"] = "http://localhost:8000"

    res = llm.llm_classify_category(
        provider="uzu",
        model="qwen3-3b",
        categories=["Receipts & Invoices", "Manuals & Guides", "Unsorted"],
        default_category="Unsorted",
        source_path="~/Downloads/invoice.pdf",
        source_basename="invoice.pdf",
        title=None,
        authors=None,
        subject=None,
        keywords=None,
        page_count=None,
        text_sample="Invoice Total Due",
        timeout_seconds=5.0,
        max_output_tokens=64,
        path_mode="basename",
        path_tail_parts=3,
    )

    assert res.category == "Receipts & Invoices", res
    assert abs(res.confidence - 0.85) < 1e-9, res
    assert "invoice" in res.reason.lower(), res

    print("OK: uzu provider parsing + JSON extraction works.")


if __name__ == "__main__":
    main()


