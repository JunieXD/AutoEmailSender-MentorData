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
    assert 'gh issue close "$ISSUE_NUMBER"' in text


def test_finalization_closes_the_source_issue_after_success() -> None:
    path = PROJECT_ROOT / ".github" / "workflows" / "finalize-moderation.yml"
    text = path.read_text(encoding="utf-8")
    assert 'gh issue close "${{ steps.branch.outputs.issue_number }}"' in text
    assert "--reason completed" in text
