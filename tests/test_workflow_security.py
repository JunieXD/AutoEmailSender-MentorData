from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PINNED_ACTION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
FORBIDDEN_DIRECT_CONTEXT = (
    "${{ github.event.issue.body }}",
    "${{ github.event.issue.title }}",
    "${{ github.event.pull_request.title }}",
    "${{ github.event.pull_request.body }}",
    "${{ github.event.comment.body }}",
)
ISSUE_WORKFLOWS = (
    "process-mentor-issue.yml",
    "process-batch-issue.yml",
    "process-report-issue.yml",
)


def _walk_uses(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "uses":
                yield item
            else:
                yield from _walk_uses(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_uses(item)


def test_workflows_are_valid_yaml_and_external_actions_are_sha_pinned() -> None:
    workflows = sorted((PROJECT_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for path in workflows:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(document, dict), path
        for value in _walk_uses(document):
            if not isinstance(value, str):
                continue
            if value.startswith("./"):
                continue
            if "/" in value and "@" in value and not value.startswith("${{"):
                assert PINNED_ACTION_PATTERN.fullmatch(value), (path, value)


def test_untrusted_issue_and_pull_request_text_is_never_interpolated_into_run_scripts() -> None:
    workflows = sorted((PROJECT_ROOT / ".github" / "workflows").glob("*.yml"))
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DIRECT_CONTEXT:
            assert forbidden not in text, (path, forbidden)


def test_issue_intake_is_isolated_per_issue_and_promotion_is_serialized() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    expected_groups = {
        "process-mentor-issue.yml": "process-mentor-",
        "process-batch-issue.yml": "process-batch-",
        "process-report-issue.yml": "process-report-",
    }
    for filename, prefix in expected_groups.items():
        document = yaml.load(
            (workflow_root / filename).read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        concurrency = document["jobs"]["prepare"]["concurrency"]
        assert concurrency["group"].startswith(prefix)
        assert concurrency["cancel-in-progress"] == "false"

    promotion = yaml.load(
        (workflow_root / "promote-ready-pulls.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert promotion["concurrency"]["group"] == "mentor-data-promotion"
    assert promotion["concurrency"]["cancel-in-progress"] == "false"
    assert "schedule" in promotion["on"]


def test_issue_workflows_only_create_stable_proposal_pull_requests() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for filename in ISSUE_WORKFLOWS:
        text = (workflow_root / filename).read_text(encoding="utf-8")
        assert "scripts/issue_workflow_state.py" in text
        assert "steps.workflow_state.outputs.branch" in text
        assert "scripts/create_issue_pull_request.py" in text
        assert "RUN_ID" not in text
        assert "RUN_ATTEMPT" not in text
        assert "gh pr merge" not in text
        assert "gh workflow run pages.yml" not in text
        assert "finalize-proposal" not in text


def test_batch_workflow_stages_manifest_only_when_every_row_is_invalid() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "process-batch-issue.yml"
    text = path.read_text(encoding="utf-8")

    assert 'git add -- "reviews/pending/batch-issue-${ISSUE_NUMBER}.json"' in text
    assert 'if [ "$PROPOSAL_COUNT" -gt 0 ]; then' in text


def test_promotion_uses_trusted_default_branch_and_durable_fallback_triggers() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "promote-ready-pulls.yml"
    text = path.read_text(encoding="utf-8")
    assert "ref: main" in text
    assert "scripts/promote_ready_pull_requests.py" in text
    assert "workflow_run:" in text
    assert "issue_comment:" in text
    assert "pull_request_target:" in text
    assert "schedule:" in text
    assert "github.event.comment.body" not in "\n".join(
        line for line in text.splitlines() if line.lstrip().startswith("run:")
    )
    assert "gh workflow run pages.yml" in text


def test_issue_workflows_create_at_most_one_status_comment() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for filename in ISSUE_WORKFLOWS:
        text = (workflow_root / filename).read_text(encoding="utf-8")
        assert "types: [opened]" in text
        assert "reopened" not in text
        assert text.count("gh issue comment") == 1
        assert "<!-- mentor-data-status:v1 -->" in text
        assert "Post the only Issue status notification" in text
        assert "WORKFLOW_OUTCOME" in text
        assert '"$WORKFLOW_OUTCOME" = "existing_pull"' in text
        assert "outputs.outcome != 'finalized'" in text


def test_issue_status_urls_use_markdown_links_instead_of_bare_url_punctuation() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    attached_punctuation = re.compile(r"\$\{(?:PR_URL|REVIEW_URL)\}[，。；：！？]")
    for filename in ISSUE_WORKFLOWS:
        text = (workflow_root / filename).read_text(encoding="utf-8")
        assert attached_punctuation.search(text) is None
        assert "](${" in text


def test_issue_workflows_support_safe_maintainer_retries_and_exact_pr_titles() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for filename in ISSUE_WORKFLOWS:
        text = (workflow_root / filename).read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text
        assert "Resolve source Issue event" in text
        assert '--jq \'{action: "opened", issue: .}\'' in text
        assert '--event "$SOURCE_EVENT"' in text
        assert "gh pr create" not in text
        assert "GITHUB_TOKEN: ${{ github.token }}" in text
        assert "--force-with-lease" in text


def test_redundant_pull_request_check_workflows_are_removed() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    assert not (workflow_root / "check-proposals.yml").exists()
    assert not (workflow_root / "finalize-moderation.yml").exists()
    assert not (workflow_root / "apply-organization-review.yml").exists()
    validation = yaml.load(
        (workflow_root / "validate.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert "pull_request" not in validation["on"]


def test_pages_reconciles_all_finalized_issues_only_after_deploy() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "pages.yml"
    text = path.read_text(encoding="utf-8")
    document = yaml.load(text, Loader=yaml.BaseLoader)
    assert "scripts/finalized_issue_numbers.py" in text
    assert "needs.deploy.result == 'success'" in text
    assert 'gh issue close "$ISSUE_NUMBER"' in text
    assert "--reason completed" in text
    assert "inputs.issue_number" not in text
    assert "mentor-data build --output .work/pages" in text
    assert "mentor-data stage-current --archive .work/pages --output dist" in text
    assert "diff --cached --quiet" in text
    assert document["concurrency"]["cancel-in-progress"] == "true"
    upload_step = next(
        step
        for step in document["jobs"]["build"]["steps"]
        if step.get("name") == "Upload Pages artifact"
    )
    assert upload_step["with"]["path"] == "dist"
