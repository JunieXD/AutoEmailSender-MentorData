from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mentor_data.agent_review import (
    AgentReviewError,
    PullSnapshot,
    _source_root,
    assert_draft_current,
    decision_comment_body,
    plan_review,
    project_fields,
    proposed_organization_id,
    validate_answer,
)
from mentor_data.agent_review_github import GitHubReviewClient
from mentor_data.agent_review_preflight import run_preflight
from mentor_data.organization_review import (
    ReviewComment,
    ReviewPull,
    _parse_review_comment_payload,
    _validate_schema,
    apply_organization_review,
)

from .helpers import build_test_repository
from .test_organization_review import REPOSITORY, _prepare, _row
from .test_promotion import (
    _initialize_local_git_repository,
    _LocalGitHubRunner,
    _organization_review_decision,
    _prepare_batch_pull,
    _pull_payload,
    _stage_internal_pull_branch,
)


def _pull(number: int, *, issue_number: int) -> PullSnapshot:
    return PullSnapshot(
        number=number,
        issue_number=issue_number,
        title="[批量投稿] Agent 审核测试",
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        branch=f"batch/issue-{issue_number}",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )


def _plan(
    manifest: dict,
    manifest_path: Path,
    *,
    issue_number: int,
    answers: dict | None = None,
) -> dict:
    return plan_review(
        repository=REPOSITORY,
        pull=_pull(88, issue_number=issue_number),
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        previous_answers=answers,
    )


def _apply(root: Path, issue_number: int, decision: dict):
    comment = ReviewComment(
        pull_request_number=88,
        comment_id=901,
        reviewer_id=999,
        reviewer_login="maintainer",
        author_association="OWNER",
        created_at=datetime(2026, 8, 3, 1, tzinfo=UTC),
        decision=decision,
    )
    pull = ReviewPull(
        number=88,
        issue_number=issue_number,
        head_ref=f"batch/issue-{issue_number}",
        repository=REPOSITORY,
    )
    return apply_organization_review(root, comment, pull)


def test_plan_auto_resolves_exact_parents_and_clear_department(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "自动老师",
                "auto@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/auto",
                department="软件工程系",
            )
        ],
        number=70,
    )

    draft = _plan(manifest, manifest_path, issue_number=70)

    assert draft["summary"] == {
        "groups": 1,
        "rows": 1,
        "invalid_rows": 0,
        "auto_groups": 1,
        "answered_groups": 0,
        "pending_groups": 0,
        "questions": 0,
        "pending_questions": 0,
        "complete": True,
    }
    decision = draft["decision"]
    _validate_schema(
        root,
        "organization-review-decision.schema.json",
        decision,
        "Agent 审核决定",
    )
    group_decision = decision["decisions"][0]
    assert [item["action"] for item in group_decision["levels"]] == [
        "existing",
        "existing",
        "create",
    ]
    assert draft["groups"][0]["auto_rules"] == [
        "exact_university_match",
        "exact_school_match",
        "clear_new_department_department",
    ]
    applied = _apply(root, 70, decision)
    assert applied.ready_for_finalization is True
    assert applied.created_organizations == 1


def test_school_name_in_department_waits_for_user_then_creates_sibling(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "层级老师",
                "level@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/level",
                department="人工智能学院",
            )
        ],
        number=71,
    )
    first = _plan(manifest, manifest_path, issue_number=71)
    question = first["questions"][0]

    assert first["decision"] is None
    assert question["type"] == "school_level_in_department"
    assert question["recommendation"] == "create-sibling"

    second = _plan(
        manifest,
        manifest_path,
        issue_number=71,
        answers={question["id"]: {"choice": "create-sibling"}},
    )

    assert second["summary"]["pending_questions"] == 0
    assert second["summary"]["answered_groups"] == 1
    decision = second["decision"]
    creation = decision["organization_creations"][0]
    assert creation["organization_id"] == proposed_organization_id(
        "school",
        "人工智能学院",
        "org_example_university",
    )
    assert decision["decisions"][0]["mapping_kind"] == "department_as_school"
    _validate_schema(
        root,
        "organization-review-decision.schema.json",
        decision,
        "Agent 审核决定",
    )
    applied = _apply(root, 71, decision)
    assert applied.ready_for_finalization is True
    assert applied.created_organizations == 1


def test_ambiguous_department_can_map_directly_to_any_existing_organization(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "校领导",
                "leader@example.edu",
                "示例大学",
                "计算机学院",
                "https://www.example.edu/leader",
                department="校领导",
            )
        ],
        number=72,
    )
    first = _plan(manifest, manifest_path, issue_number=72)
    question = first["questions"][0]
    second = _plan(
        manifest,
        manifest_path,
        issue_number=72,
        answers={
            question["id"]: {
                "choice": "map-existing",
                "organization_id": "org_example_university",
                "reason": "校领导归入学校层级",
            }
        },
    )

    group_decision = second["decision"]["decisions"][0]
    assert group_decision["levels"] == []
    assert group_decision["target_organization_id"] == "org_example_university"
    assert group_decision["mapping_kind"] == "custom"
    assert _apply(root, 72, second["decision"]).ready_for_finalization is True


def test_department_repeating_university_maps_to_university(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "学校直属老师",
                "direct@example.edu",
                "示例大学",
                "计算机学院",
                "https://www.example.edu/direct",
                department="示例大学",
            )
        ],
        number=75,
    )

    draft = _plan(manifest, manifest_path, issue_number=75)

    assert draft["summary"]["complete"] is True
    assert draft["groups"][0]["auto_rules"][-1] == "repeated_university_name"
    group_decision = draft["decision"]["decisions"][0]
    assert [item["action"] for item in group_decision["levels"]] == [
        "existing",
        "skip",
        "skip",
    ]
    assert _apply(root, 75, draft["decision"]).ready_for_finalization is True


def test_comment_body_uses_backend_compatible_payload(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "评论老师",
                "comment@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/comment",
            )
        ],
        number=73,
    )
    decision = _plan(manifest, manifest_path, issue_number=73)["decision"]

    body = decision_comment_body(decision)
    parsed = _parse_review_comment_payload(body)

    assert body.startswith("<!-- mentor-data-organization-review:v1 -->")
    assert parsed["pull_request_number"] == 88
    assert parsed["issue_number"] == 73
    assert len(body) < 65_536


def test_answer_validation_field_projection_and_stale_guard() -> None:
    question = {
        "id": "q_test",
        "options": [
            {
                "value": "map-existing",
                "label": "映射",
                "requires": ["organization_id"],
            }
        ],
    }
    with pytest.raises(AgentReviewError, match="organization_id"):
        validate_answer(question, {"choice": "map-existing"})
    validate_answer(
        question,
        {"choice": "map-existing", "organization_id": "org_example_cs"},
    )

    assert project_fields({"id": "x", "path": "y"}, ["id"]) == {"id": "x"}
    with pytest.raises(AgentReviewError, match="未知输出字段"):
        project_fields({"id": "x"}, ["rows"])

    pull = _pull(88, issue_number=73)
    draft = {"pull": pull.as_dict(), "manifest_sha256": "c" * 64}
    assert_draft_current(draft, pull, "c" * 64)
    changed = _pull(88, issue_number=73)
    changed_value = changed.as_dict()
    changed_value["head_sha"] = "d" * 40
    changed_pull = PullSnapshot(**changed_value)
    with pytest.raises(AgentReviewError, match="已变化"):
        assert_draft_current(draft, changed_pull, "c" * 64)


def test_new_university_home_uses_discovery_domain_not_external_profile() -> None:
    group = {
        "source_domains": ["faculty.example.edu"],
        "source_urls": [
            "https://a.external.test/mentor",
            "https://faculty.example.edu/teachers/list",
        ],
    }

    assert _source_root(group) == "https://faculty.example.edu/"


def test_preflight_reuses_trusted_review_and_finalization_pipeline(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    base_sha = _initialize_local_git_repository(root, tmp_path)
    issue_number = 74
    paths, manifest = _prepare_batch_pull(
        root,
        tmp_path,
        issue_number=issue_number,
        valid=True,
    )
    manifest_path = paths[-1]
    decision = _organization_review_decision(issue_number, manifest_path, manifest)
    branch = f"batch/issue-{issue_number}"
    head_sha = _stage_internal_pull_branch(root, branch, paths)
    payload = _pull_payload(
        number=88,
        issue_number=issue_number,
        kind="batch",
        draft=True,
    )
    payload["head"]["sha"] = head_sha
    payload["base"]["sha"] = base_sha
    runner = _LocalGitHubRunner(root, payload)
    client = GitHubReviewClient(
        repository=REPOSITORY,
        root=root,
        runner=runner,
    )
    pull = PullSnapshot(
        number=88,
        issue_number=issue_number,
        title=payload["title"],
        url=payload["html_url"],
        branch=branch,
        head_sha=head_sha,
        base_sha=base_sha,
        draft=True,
        status_label="status:manual-review",
    )

    result = run_preflight(
        root=root,
        client=client,
        pull=pull,
        decision=decision,
    )

    assert result["ok"] is True
    assert result["mapped_proposals"] == 1
    assert result["finalized_proposals"] == 1
    assert result["main_sha"] == base_sha
    assert not list(root.parent.glob("mentor-data-agent-review-*"))
