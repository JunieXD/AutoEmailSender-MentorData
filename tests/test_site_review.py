from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_review_page_has_strict_external_script_and_connection_policy() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    assert '<script src="review.js" defer></script>' in html
    assert "<script>" not in html
    assert "https://api.github.com" in html
    assert "https://raw.githubusercontent.com" in html
    assert "script-src 'self'" in html
    assert "form-action 'none'" in html


def test_review_script_treats_manifest_as_text_and_outputs_fixed_comment_marker() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "eval(" not in script
    assert "mentor-data-organization-review:v1" in script
    assert "crypto.subtle.digest" in script
    assert "textContent" in script
    assert "MAX_COMMENT_BYTES" in script
    assert "validateWebUrl" in script
    assert '["http:", "https:"]' in script
