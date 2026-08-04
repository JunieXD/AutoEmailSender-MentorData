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


def test_review_uses_custom_selection_controls_and_bounded_row_scrolling() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert 'element("select"' not in script
    assert 'createElement("select")' not in script
    assert "datalist" not in html
    assert "datalist" not in script
    assert "createOrganizationPicker" in script
    assert 'setAttribute("role", "combobox")' in script
    assert "max-height: min(21rem, 50vh)" in styles
    assert "scrollbar-gutter: stable" in styles


def test_review_links_each_mentor_to_a_safely_resolved_profile() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert "createMentorProfileButton" in script
    assert "resolveMentorProfileUrl" in script
    assert "profileWindow.opener = null" in script
    assert "MAX_PROPOSAL_BYTES" in script
    assert ".mentor-profile-button" in styles


def test_review_reuses_organization_drafts_and_autosaves_progress() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert 'id="organization-tree"' in html
    assert 'id="next-pending"' in html
    assert 'id="autosave-status"' in html
    assert "buildOrganizationDrafts" in script
    assert "organizationDraftKey" in script
    assert "localStorage.setItem" in script
    assert "restoreReviewDraft" in script
    assert "同一机构只需确认一次" in script


def test_review_suggests_official_urls_and_lists_pending_destinations() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "suggestedOfficialUrl" in script
    assert "官方网站（没有可以留空）" in script
    assert "refreshPendingOrganizationOptions" in script
    assert "本次新建" in script
    assert "改到其他机构" in script


def test_review_lazily_renders_large_mentor_groups_and_throttles_autosave() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "card.renderedRowCount + 100" in script
    assert 'rowsDetails.addEventListener("toggle"' in script
    assert "rowEditorByProposalId" in script
    assert "autosaveDirty" in script
    assert "window.setTimeout(saveReviewDraft, 800)" in script


def test_public_home_page_prioritizes_using_contributing_and_correcting_data() -> None:
    html = (PROJECT_ROOT / "site" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "app.js").read_text(encoding="utf-8")

    assert "不必每次重新抓取" in html
    assert "下载 Auto Email Sender" in html
    assert "贡献导师数据" in html
    assert "反馈错误或过时信息" in html
    assert 'id="university-count"' in html
    assert 'id="unit-count"' in html
    assert "review.html" not in html
    assert "innerHTML" not in script
    assert "safeCatalogUrl" in script
    assert "unit.path" not in script
