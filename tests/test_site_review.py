from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_review_page_has_strict_external_script_and_connection_policy() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="styles.css?v=9" />' in html
    assert '<script src="review-logic.js?v=9" defer></script>' in html
    assert '<script src="review.js?v=9" defer></script>' in html
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
    assert "Array.from(compactBody).length" in script
    assert "JSON.stringify(decision)" in script
    assert "compactDecisionForComment" in script
    assert "已精简" in script
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
    assert "与上级同名，已归入上级" in script
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

    assert 'id="review-organization-tree"' in html
    assert 'id="confirm-review-task"' in html
    assert 'id="autosave-status"' in html
    assert "buildOrganizationDrafts" in script
    assert "organizationDraftKey" in script
    assert "localStorage.setItem" in script
    assert "restoreReviewDraft" in script
    assert 'nodes.autosaveStatus.textContent = "已保存"' in script


def test_review_suggests_official_urls_and_lists_pending_destinations() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "suggestedOfficialUrl" in script
    assert "官网（可留空）" in script
    assert "refreshPendingOrganizationOptions" in script
    assert "本次新建" in script
    assert "单独调整到其他机构" in script
    assert "已匹配" in script


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
    assert "确认任职关系" in script
    assert "append_current_affiliation" in script
    assert "transfer_current_affiliation" in script
    assert "collectIdentityResolutions" in script
    assert "identity_resolutions" in script
    assert "增加双聘任职" in script
    assert "任职调动" in script
    assert ".identity-resolution" in styles
    assert ".identity-comparison" in styles
    assert "IDENTITY_CONFLICT_REJECTION_REASON" in script
    assert 'action.value = "reject"' in script
    assert "identity-comparison-table" in script
    assert "pathText(card.group)" in script


def test_review_compares_existing_record_fields_and_rejects_conflicts_by_default() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert "导师待确认" in html
    assert "RECORD_CONFLICT_REJECTION_REASON" in script
    assert "RECORD_CONFLICT_FIELD_LABELS" in script
    assert "createRecordConflictPanel" in script
    assert "recordConflictRejectionReason" in script
    assert "本次投稿应不收录" in script
    assert "如需更新资料，请提交信息纠错。" in script
    assert "pendingConflictSourceIsRejected" in script
    assert "保留本次，拒绝第" in script
    assert ".record-conflicts" in styles
    assert ".record-conflict-comparison" in styles
    assert '.record-conflict[data-state="replacement"]' in styles


def test_review_accepts_pending_affiliation_labels_for_new_batch_mentors() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")

    assert "hasPendingOrganization" in script
    assert "affiliation?.organization_id === null" in script
    assert "affiliation.organization_label.trim().length > 0" in script


def test_review_supports_path_correction_and_independent_targets() -> None:
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    logic = (PROJECT_ROOT / "site" / "review-logic.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")
    adjustment_start = script.index("function openGroupAdjustment(card)")
    adjustment_block = script[adjustment_start : adjustment_start + 500]

    assert "suggested_path_correction" in script
    assert "整组调整归属" in script
    assert "仅调整个别导师" in script
    assert "organization_creations" in script
    assert "target_organization_id" in script
    assert "save_path_correction" in script
    assert "validateFinalAssignmentSources" in script
    assert "hasOfficialEvidence" in logic
    assert "correctionDefaults" in logic
    assert "requiredSubmittedLevels" in logic
    assert "mergeIndependentCreations" in logic
    assert ".independent-target-panel" in styles
    assert ".path-correction-notice" in styles
    assert 'card.groupAction.value = "resolve"' in adjustment_block
    assert "updateGroupCard(card)" in adjustment_block


def test_review_uses_organization_tree_and_plain_reviewer_language() -> None:
    html = (PROJECT_ROOT / "site" / "review.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "site" / "review.js").read_text(encoding="utf-8")
    logic = (PROJECT_ROOT / "site" / "review-logic.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "site" / "styles.css").read_text(encoding="utf-8")

    assert 'id="review-workspace"' in html
    assert 'id="review-organization-tree"' in html
    assert 'id="review-tree-search"' in html
    assert 'id="workflow-pending-count"' in html
    assert 'class="review-node-main"' in html
    assert 'id="confirm-review-task"' in html
    assert "确认此节点并继续" in html
    assert "review-result-summary" not in html
    assert "审核导师投稿" in html
    assert "系统会先整理没有争议的内容" not in html
    assert "选择处理结果后" not in html
    assert "归入已有机构" in script
    assert "归入上级" in script
    assert "这个选择会用于" not in script
    assert "没有发现机构层级或导师任职方面的冲突" not in script
    assert "taskContext" in script
    assert "buildWorkflowTasks" in script
    assert "buildWorkflowNodes" in script
    assert "renderWorkflowTree" in script
    assert "renderWorkflowNode" in script
    assert "applySuggestedGroupDecision" in script
    assert "applyInitialPathSuggestions" in script
    assert "focusNextWorkflowTask" in script
    assert "额外官方来源域名" not in script
    assert "pathReviewSuggestion" in logic
    assert "rankOrganizationCandidates" in logic
    assert "rankOrganizationSearchResults" in logic
    assert "workflowNodeIdForDraft" in script
    assert "review-tree-node" in script
    assert "completed_at" in script
    assert "focusReviewValidationError" in script
    assert "organization-create-context" in script
    assert "忽略非官网详情页" in script
    assert "移到其他学院" in script
    assert "不收录这组" in script
    assert "siblingOrganizationCandidate" in logic
    assert "schoolLevelPlacementDefault" in logic
    assert 'action: "create_sibling"' in logic
    assert 'action: "reject_group"' in logic
    assert ".review-workspace-grid" in styles
    assert ".review-node-main" in styles
    assert ".review-tree-node" in styles
    assert ".node-workbench-section" in styles
    assert "height: 2.3rem" in styles
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
