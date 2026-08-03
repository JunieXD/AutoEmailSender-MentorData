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


def test_every_repository_writer_uses_the_shared_concurrency_group() -> None:
    writers = {
        "process-mentor-issue.yml",
        "process-batch-issue.yml",
        "process-report-issue.yml",
        "finalize-moderation.yml",
        "apply-organization-review.yml",
        "revoke-contributor.yml",
    }
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for filename in writers:
        document = yaml.load(
            (workflow_root / filename).read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        assert document["concurrency"]["group"] == "mentor-data-write"
        assert document["concurrency"]["cancel-in-progress"] == "false"


def test_organization_review_runs_trusted_code_and_never_embeds_decision_in_shell() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "apply-organization-review.yml"
    text = path.read_text(encoding="utf-8")
    assert ".trusted/.venv/bin/mentor-data apply-organization-review" in text
    assert "github.event.comment.body" not in "\n".join(
        line for line in text.splitlines() if line.lstrip().startswith("run:")
    )
    assert '--expected-repository "$GITHUB_REPOSITORY"' in text
    assert 'git push origin "HEAD:${HEAD_REF}"' in text
    assert 'gh pr merge "$PR_NUMBER"' in text
    assert '--match-head-commit "$HEAD_SHA"' in text
    assert "gh workflow run finalize-moderation.yml" in text
    assert 'gh issue close "$ISSUE_NUMBER"' in text
    assert "gh issue comment" not in text
    assert "gh pr comment" not in text


def test_issue_workflows_create_at_most_one_status_comment() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for filename in ISSUE_WORKFLOWS:
        text = (workflow_root / filename).read_text(encoding="utf-8")
        assert "types: [opened]" in text
        assert "reopened" not in text
        assert text.count("gh issue comment") == 1
        assert "<!-- mentor-data-status:v1 -->" in text
        assert "Post the only Issue status notification" in text


def test_downstream_moderation_never_adds_progress_comments() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for filename in ("apply-organization-review.yml", "finalize-moderation.yml"):
        text = (workflow_root / filename).read_text(encoding="utf-8")
        assert "gh issue comment" not in text
        assert "gh pr comment" not in text


def test_finalization_dispatches_publication_with_source_issue() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "finalize-moderation.yml"
    text = path.read_text(encoding="utf-8")
    assert '--moderator-id "$MODERATOR_ID"' in text
    assert "steps.branch.outputs.pending == 'true'" in text
    assert "steps.branch.outputs.finalized == 'true'" in text
    assert "gh workflow run pages.yml" in text
    assert '-f "issue_number=$ISSUE_NUMBER"' in text
    assert "gh issue comment" not in text


def test_pages_refuses_intermediate_data_and_closes_only_after_deploy() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "pages.yml"
    text = path.read_text(encoding="utf-8")
    assert "python3 scripts/publication_metadata.py" in text
    assert "needs.gate.outputs.publish == 'true'" in text
    assert "needs.deploy.result == 'success'" in text
    assert 'gh issue close "$ISSUE_NUMBER"' in text
    assert "--reason completed" in text


def test_draft_pull_requests_do_not_run_redundant_checks() -> None:
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    proposal_check = (workflow_root / "check-proposals.yml").read_text(encoding="utf-8")
    validation = (workflow_root / "validate.yml").read_text(encoding="utf-8")
    assert "if: github.event.pull_request.draft == false" in proposal_check
    assert (
        "if: github.event_name != 'pull_request' || github.event.pull_request.draft == false"
        in validation
    )
