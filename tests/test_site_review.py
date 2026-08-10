from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_review_page_has_strict_external_script_and_connection_policy() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="styles.css?v=2" />' in html
    assert '<script src="review-logic.js?v=2" defer></script>' in html
    assert '<script src="review.js?v=2" defer></script>' in html
    assert "<script>" not in html
    assert "https://api.github.com" in html
    assert "https://raw.githubusercontent.com" in html
    assert "script-src 'self'" in html
    assert "form-action 'none'" in html


def test_report_review_page_is_safe_and_never_requires_json_editing() -> None:
    html = (PROJECT_ROOT / "site" / "report-review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "report-review.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert '<script src="report-review-logic.js" defer></script>' in html
    assert '<script src="report-review.js" defer></script>' in html
    assert "<script>" not in html
    assert "https://api.github.com" in html
    assert "https://raw.githubusercontent.com" not in html
    assert "script-src 'self'" in html
    assert "form-action 'none'" in html
    assert "mentor-data-report-review:v1" in script
    assert "crypto.subtle.digest" in script
    assert "/contents/${proposalPath}" in script
    assert "MentorReportReviewLogic.decodeGitHubFile" in script
    assert "raw.githubusercontent.com" not in script
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "eval(" not in script
    assert 'Object.keys(proposal.accepted || {}).length !== 0' in script
    assert 'decision: "rejected"' not in script
    assert "无需编辑 JSON 或手动合并" in html
    assert ".report-comparison" in styles
    assert ".decision-choices" in styles


def test_review_script_treats_manifest_as_text_and_outputs_fixed_comment_marker() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "eval(" not in script
    assert "mentor-data-organization-review:v1" in script
    assert "crypto.subtle.digest" in script
    assert "textContent" in script
    assert "GITHUB_COMMENT_CHARACTER_LIMIT = 65_536" in script
    assert "Array.from(body).length" in script
    assert "JSON.stringify(decision)" in script
    assert "validateWebUrl" in script
    assert '["http:", "https:"]' in script
    assert r"^batch\/issue-" in script


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
    assert "handleFloatingControlScroll" in script
    assert "current.popup.contains(event.target)" in script
    assert "max-height: min(21rem, 50vh)" in styles
    assert "scrollbar-gutter: stable" in styles
    assert "overscroll-behavior: contain" in styles


def test_review_infers_new_organization_defaults_from_names() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "inferOrganizationType" in script
    assert '["研究院", "institute"]' in script
    assert '["学院", "school"]' in script
    assert '["实验室", "laboratory"]' in script
    assert '["中心", "center"]' in script
    assert '["系", "department"]' in script
    assert "shouldDefaultOrganizationToParent" in script
    assert "这一层与上级名称相同，系统已归入上级" in script
    assert "actionManuallySelected" in script
    assert "organizationTypeManuallySelected" in script


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
    assert "相关机构和导师归属已经同步更新" in script


def test_review_suggests_official_urls_and_lists_pending_destinations() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "suggestedOfficialUrl" in script
    assert "官方网站（没有可以留空）" in script
    assert "refreshPendingOrganizationOptions" in script
    assert "本次新建" in script
    assert "单独调整到其他机构" in script
    assert "已找到同名机构，自动归到" in script


def test_review_lazily_renders_large_mentor_groups_and_throttles_autosave() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "card.renderedRowCount + 100" in script
    assert 'rowsDetails.addEventListener("toggle"' in script
    assert "rowEditorByProposalId" in script
    assert "autosaveDirty" in script
    assert "window.setTimeout(saveReviewDraft, 800)" in script


def test_review_prioritizes_affiliation_conflicts_and_emits_separate_decisions() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert 'id="review-identity-count"' in html
    assert "同一位导师出现了不同的机构归属" in script
    assert "append_current_affiliation" in script
    assert "transfer_current_affiliation" in script
    assert "collectIdentityResolutions" in script
    assert "identity_resolutions" in script
    assert "增加双聘任职" in script
    assert "任职调动" in script
    assert ".identity-resolution" in styles
    assert ".identity-comparison" in styles


def test_review_accepts_pending_affiliation_labels_for_new_batch_mentors() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "hasPendingOrganization" in script
    assert "affiliation?.organization_id === null" in script
    assert "affiliation.organization_label.trim().length > 0" in script


def test_review_supports_path_correction_and_independent_targets() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    logic = (PROJECT_ROOT / "site" / "review-logic.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert "suggested_path_correction" in script
    assert "调整整组的最终归属" in script
    assert "仅为个别导师准备其他机构" in script
    assert "organization_creations" in script
    assert "target_organization_id" in script
    assert "save_path_correction" in script
    assert "validateFinalAssignmentSources" in script
    assert "correctionDefaults" in logic
    assert "requiredSubmittedLevels" in logic
    assert "mergeIndependentCreations" in logic
    assert ".independent-target-panel" in styles
    assert ".path-correction-notice" in styles


def test_review_uses_one_task_workspace_and_plain_reviewer_language() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    logic = (PROJECT_ROOT / "site" / "review-logic.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert 'id="review-workspace"' in html
    assert 'id="review-task-list"' in html
    assert 'id="workflow-pending-count"' in html
    assert 'id="result-summary-title"' in html
    assert "一次处理一件事" in html
    assert "归入已有机构" in script
    assert "不单独建立，归入上级" in script
    assert "确认这项处理" in script
    assert "buildWorkflowTasks" in script
    assert "applySuggestedGroupDecision" in script
    assert "focusNextWorkflowTask" in script
    assert "pathReviewSuggestion" in logic
    assert "rankOrganizationCandidates" in logic
    assert ".review-workspace-grid" in styles
    assert ".review-workflow-bar" in styles


def test_public_home_page_prioritizes_using_contributing_and_correcting_data() -> None:
    html = (PROJECT_ROOT / "site" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "app.js").read_text(encoding="utf-8")

    assert "共建社区导师库" in html
    assert "直接导入社区导师库信息" in html
    assert "下载 Auto Email Sender" in html
    assert "贡献导师数据" in html
    assert "反馈错误或过时信息" in html
    assert 'id="university-count"' in html
    assert 'id="unit-count"' in html
    assert "review.html" not in html
    assert "innerHTML" not in script
    assert "safeCatalogUrl" in script
    assert "unit.path" not in script
