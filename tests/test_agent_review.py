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
    university_domain_from_source,
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
    latest_organizations: list[dict] | None = None,
) -> dict:
    return plan_review(
        repository=REPOSITORY,
        pull=_pull(88, issue_number=issue_number),
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        previous_answers=answers,
        latest_organizations=latest_organizations,
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
    assert draft["organization_change_preview"] == [
        {
            "action": "create",
            "id": proposed_organization_id(
                "department",
                "软件工程系",
                "org_example_cs",
            ),
            "type": "department",
            "path": "示例大学 / 计算机学院 / 软件工程系",
            "source": "rule",
            "source_domains": ["cs.example.edu"],
            "official_urls": [],
            "approved_domains": [],
        }
    ]
    applied = _apply(root, 70, decision)
    assert applied.ready_for_finalization is True
    assert applied.created_organizations == 1


def test_new_descendants_request_existing_ancestor_domain_approval(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "域名老师",
                "domain@nankai.edu.cn",
                "示例大学",
                "安全学院",
                "https://cc.nankai.edu.cn/faculty/domain",
                department="安全系",
            )
        ],
        number=79,
    )

    first = _plan(manifest, manifest_path, issue_number=79)

    assert first["decision"] is None
    question = first["questions"][0]
    assert question["type"] == "source_domain_mismatch"
    assert question["context"] == {
        "target_organization_id": "org_example_university",
        "source_domains": ["cc.nankai.edu.cn"],
        "approved_domains": ["example.edu"],
    }

    second = _plan(
        manifest,
        manifest_path,
        issue_number=79,
        answers={question["id"]: {"choice": "approve-domains"}},
    )

    assert second["summary"]["pending_questions"] == 0
    assert second["decision"] is not None
    levels = second["decision"]["decisions"][0]["levels"]
    assert [item["action"] for item in levels] == ["create", "create", "create"]
    assert levels[0]["canonical_name"] == "示例大学"
    assert levels[0]["approved_domains"] == ["nankai.edu.cn"]
    applied = _apply(root, 79, second["decision"])
    assert applied.ready_for_finalization is True
    assert applied.updated_organizations == 1


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
    assert question["rule_default"] == "create-sibling"
    assert question["context_recommendation"] is None
    assert question["recommendation_confidence"] is None
    assert question["path_correction_scopes"] == [
        "current-batch",
        "future-identical-path",
    ]
    assert question["path_correction_choices"] == [
        "use-suggested",
        "map-sibling",
        "create-sibling",
    ]
    create_child = next(item for item in question["options"] if item["value"] == "create-child")
    assert create_child["optional"] == [
        "canonical_name",
        "official_url",
        "approved_domains",
    ]

    second = _plan(
        manifest,
        manifest_path,
        issue_number=71,
        answers={
            question["id"]: {
                "choice": "create-sibling",
                "save_path_correction": True,
            }
        },
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
    assert decision["decisions"][0]["save_path_correction"] is True
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
    assert draft["path_normalizations"] == [
        {
            "group_id": draft["groups"][0]["id"],
            "submitted_path": "示例大学 / 计算机学院 / 示例大学",
            "resolved_path": "示例大学",
            "row_count": 1,
            "source": "rule",
            "rules": ["repeated_university_name"],
            "path_correction_scope": None,
        }
    ]
    assert _apply(root, 75, draft["decision"]).ready_for_finalization is True


def test_empty_department_is_visible_in_path_normalization_summary(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "学院老师",
                "school-only@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/school-only",
            )
        ],
        number=751,
    )

    draft = _plan(manifest, manifest_path, issue_number=751)

    assert draft["path_normalizations"] == [
        {
            "group_id": draft["groups"][0]["id"],
            "submitted_path": "示例大学 / 计算机学院",
            "resolved_path": "示例大学 / 计算机学院",
            "row_count": 1,
            "source": "rule",
            "rules": ["empty_department"],
            "path_correction_scope": None,
        }
    ]


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
    with pytest.raises(AgentReviewError, match="不支持保存"):
        validate_answer(
            question,
            {
                "choice": "map-existing",
                "organization_id": "org_example_cs",
                "save_path_correction": True,
            },
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


def test_chinese_university_domain_collapses_only_edu_cn_subdomains() -> None:
    assert university_domain_from_source("cs.shu.edu.cn") == "shu.edu.cn"
    assert university_domain_from_source("SHU.EDU.CN.") == "shu.edu.cn"
    assert university_domain_from_source("faculty.example.edu") == "faculty.example.edu"
    assert university_domain_from_source("cs.example.com.cn") == "cs.example.com.cn"


def test_similar_new_sibling_departments_require_one_human_decision(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "短名老师",
                "short@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/short.html",
                department="智能科学技术系",
                profile_url="https://cs.example.edu/profiles/short.html",
            ),
            _row(
                "长名老师",
                "long@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/long.html",
                department="计算机学院智能科学技术系",
                profile_url="https://cs.example.edu/profiles/long.html",
            ),
        ],
        number=76,
    )

    first = _plan(manifest, manifest_path, issue_number=76)
    pending = [item for item in first["questions"] if item["status"] == "pending"]

    assert len(pending) == 1
    question = pending[0]
    assert question["type"] == "similar_new_sibling"
    assert question["rule_default"] == "keep-separate"
    assert question["context_recommendation"] == "use-canonical"
    assert question["recommendation_confidence"] == "high"
    assert question["context"]["recommended_canonical_name"] == "智能科学技术系"
    assert question["context"]["recommended_canonical_group_id"] == first["groups"][0]["id"]
    assert question["context"]["evidence"] == [
        "shared_source_directory",
        "similar_name",
    ]
    assert question["path_correction_scopes"] == [
        "current-batch",
        "future-identical-path",
    ]
    assert question["path_correction_choices"] == ["use-canonical"]

    merged = _plan(
        manifest,
        manifest_path,
        issue_number=76,
        answers={
            question["id"]: {
                "choice": "use-canonical",
                "canonical_name": "智能科学技术系",
                "save_path_correction": True,
            }
        },
    )
    merged_department_names = {
        decision["levels"][-1]["canonical_name"]
        for decision in merged["decision"]["decisions"]
    }
    assert merged_department_names == {"智能科学技术系"}
    corrected_decision = next(
        item
        for item in merged["decision"]["decisions"]
        if item["save_path_correction"]
    )
    assert corrected_decision["mapping_kind"] == "custom"
    assert corrected_decision["target_organization_id"] == proposed_organization_id(
        "department",
        "智能科学技术系",
        "org_example_cs",
    )
    assert merged["path_normalizations"] == [
        {
            "group_id": corrected_decision["group_id"],
            "submitted_path": "示例大学 / 计算机学院 / 计算机学院智能科学技术系",
            "resolved_path": "示例大学 / 计算机学院 / 智能科学技术系",
            "row_count": 1,
            "source": "user-decision",
            "rules": ["user_merge_similar_sibling"],
            "path_correction_scope": "future-identical-path",
        }
    ]
    assert _apply(root, 76, merged["decision"]).created_organizations == 1

    separate = _plan(
        manifest,
        manifest_path,
        issue_number=76,
        answers={question["id"]: {"choice": "keep-separate"}},
    )
    separate_department_names = {
        decision["levels"][-1]["canonical_name"]
        for decision in separate["decision"]["decisions"]
    }
    assert separate_department_names == {
        "智能科学技术系",
        "计算机学院智能科学技术系",
    }


def test_common_index_directory_does_not_link_unrelated_new_departments(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "智能老师",
                "intelligence@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/list.htm",
                department="智能安全系",
                profile_url="https://cs.example.edu/profiles/intelligence.html",
            ),
            _row(
                "密码老师",
                "crypto@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/list.htm",
                department="密码科学与技术系",
                profile_url="https://cs.example.edu/profiles/crypto.html",
            ),
        ],
        number=761,
    )

    draft = _plan(manifest, manifest_path, issue_number=761)

    assert draft["summary"]["pending_questions"] == 0
    assert {
        item["levels"][-1]["canonical_name"]
        for item in draft["decision"]["decisions"]
    } == {"智能安全系", "密码科学与技术系"}


def test_similar_department_context_aggregates_only_the_collision_cluster(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "规范老师",
                "canonical@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/list.htm",
                department="网络安全系",
                profile_url="https://cs.example.edu/profiles/canonical.html",
            ),
            _row(
                "变体老师",
                "variant@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/list.htm",
                department="计算机学院网络安全系",
                profile_url="https://cs.example.edu/profiles/variant.html",
            ),
            _row(
                "密码老师",
                "crypto-cluster@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/list.htm",
                department="密码科学与技术系",
                profile_url="https://cs.example.edu/profiles/crypto-cluster.html",
            ),
        ],
        number=762,
    )

    draft = _plan(manifest, manifest_path, issue_number=762)
    pending = [item for item in draft["questions"] if item["status"] == "pending"]

    assert len(pending) == 1
    question = pending[0]
    assert question["path"].endswith("计算机学院网络安全系")
    assert question["context"]["candidate_names"] == [
        "网络安全系",
        "计算机学院网络安全系",
    ]
    assert [
        item["name"] for item in question["context"]["candidate_groups"]
    ] == ["网络安全系", "计算机学院网络安全系"]
    assert question["context"]["recommended_canonical_name"] == "网络安全系"


def test_similar_department_recommendation_does_not_cross_shared_url_bridge(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "规范老师",
                "canonical-bridge@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/canonical.html",
                department="网络安全系",
            ),
            _row(
                "变体老师",
                "variant-bridge@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/shared-detail.html",
                department="计算机学院网络安全系",
            ),
            _row(
                "无关老师",
                "unrelated-bridge@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/shared-detail.html",
                department="激光技术系",
            ),
        ],
        number=763,
    )

    draft = _plan(manifest, manifest_path, issue_number=763)
    question = next(
        item for item in draft["questions"] if item["path"].endswith("计算机学院网络安全系")
    )

    assert question["context_recommendation"] == "use-canonical"
    assert question["recommendation_confidence"] == "high"
    assert question["context"]["recommended_canonical_name"] == "网络安全系"
    assert question["context"]["candidate_names"] == [
        "网络安全系",
        "计算机学院网络安全系",
    ]


def test_fuzzy_short_department_names_do_not_receive_merge_recommendation(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "信息老师",
                "information@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/information.html",
                department="信息工程系",
            ),
            _row(
                "通信老师",
                "communications@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/communications.html",
                department="通信工程系",
            ),
        ],
        number=764,
    )

    draft = _plan(manifest, manifest_path, issue_number=764)
    pending = [item for item in draft["questions"] if item["status"] == "pending"]

    assert len(pending) == 1
    assert pending[0]["context_recommendation"] is None
    assert pending[0]["recommendation_confidence"] is None


def test_creation_preview_exposes_same_name_under_another_parent(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "中心老师",
                "center@example.edu",
                "示例大学",
                "光电学院",
                "https://oe.example.edu/teachers/center.html",
                department="共享研究中心",
            )
        ],
        number=765,
    )
    latest = [
        {
            "id": "org_existing_center",
            "type": "center",
            "canonical_name": "共享研究中心",
            "parent_id": "org_example_cs",
            "aliases": [],
            "official_urls": [],
            "approved_domains": ["example.edu"],
            "lineage_ids": [
                "org_example_university",
                "org_example_cs",
                "org_existing_center",
            ],
            "lineage_names": ["示例大学", "计算机学院", "共享研究中心"],
        }
    ]

    first = _plan(
        manifest,
        manifest_path,
        issue_number=765,
        latest_organizations=latest,
    )
    question = first["questions"][0]

    assert question["type"] == "same_name_different_parent"
    assert question["context_recommendation"] is None
    assert question["context"]["candidates"][0]["id"] == "org_existing_center"

    draft = _plan(
        manifest,
        manifest_path,
        issue_number=765,
        latest_organizations=latest,
        answers={question["id"]: {"choice": "keep-placement"}},
    )

    assert draft["organization_conflicts"] == [
        {
            "kind": "same-name-different-parent",
            "requires_human_decision": False,
            "acknowledged": True,
            "proposed": {
                "id": proposed_organization_id(
                    "center",
                    "共享研究中心",
                    proposed_organization_id(
                        "school",
                        "光电学院",
                        "org_example_university",
                    ),
                ),
                "type": "center",
                "path": "示例大学 / 光电学院 / 共享研究中心",
            },
            "matches": [
                {
                    "id": "org_existing_center",
                    "type": "center",
                    "path": "示例大学 / 计算机学院 / 共享研究中心",
                    "parent_id": "org_example_cs",
                }
            ],
        }
    ]


def test_cross_parent_same_name_can_map_to_existing_organization(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "中心老师",
                "mapped-center@example.edu",
                "示例大学",
                "光电学院",
                "https://oe.example.edu/teachers/mapped-center.html",
                department="共享研究中心",
            )
        ],
        number=766,
    )
    latest = [
        {
            "id": "org_existing_center",
            "type": "center",
            "canonical_name": "共享研究中心",
            "parent_id": "org_example_cs",
            "aliases": [],
            "official_urls": [],
            "approved_domains": ["example.edu"],
            "lineage_ids": [
                "org_example_university",
                "org_example_cs",
                "org_existing_center",
            ],
            "lineage_names": ["示例大学", "计算机学院", "共享研究中心"],
        }
    ]
    first = _plan(
        manifest,
        manifest_path,
        issue_number=766,
        latest_organizations=latest,
    )
    question = first["questions"][0]

    mapped = _plan(
        manifest,
        manifest_path,
        issue_number=766,
        latest_organizations=latest,
        answers={
            question["id"]: {
                "choice": "map-existing",
                "organization_id": "org_existing_center",
            }
        },
    )

    decision = mapped["decision"]["decisions"][0]
    assert decision["target_organization_id"] == "org_existing_center"
    assert mapped["organization_conflicts"] == []


def test_canonical_name_change_triggers_cross_parent_confirmation(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "规范中心老师",
                "canonical-center@example.edu",
                "示例大学",
                "光电学院",
                "https://oe.example.edu/teachers/canonical-center.html",
                department="共享研究中心、光电学院（中心）",
            )
        ],
        number=769,
    )
    latest = [
        {
            "id": "org_existing_center",
            "type": "center",
            "canonical_name": "共享研究中心",
            "parent_id": "org_example_cs",
            "aliases": [],
            "official_urls": [],
            "approved_domains": ["example.edu"],
            "lineage_ids": [
                "org_example_university",
                "org_example_cs",
                "org_existing_center",
            ],
            "lineage_names": ["示例大学", "计算机学院", "共享研究中心"],
        }
    ]
    first = _plan(
        manifest,
        manifest_path,
        issue_number=769,
        latest_organizations=latest,
    )
    canonical_question = first["questions"][0]

    renamed = _plan(
        manifest,
        manifest_path,
        issue_number=769,
        latest_organizations=latest,
        answers={
            canonical_question["id"]: {
                "choice": "create-submitted",
                "organization_type": "center",
                "canonical_name": "共享研究中心",
            }
        },
    )
    pending = [item for item in renamed["questions"] if item["status"] == "pending"]

    assert renamed["decision"] is None
    assert len(pending) == 1
    assert pending[0]["type"] == "same_name_different_parent"
    assert pending[0]["context"]["proposed"]["path"] == (
        "示例大学 / 光电学院 / 共享研究中心"
    )

    confirmed = _plan(
        manifest,
        manifest_path,
        issue_number=769,
        latest_organizations=latest,
        answers={
            canonical_question["id"]: {
                "choice": "create-submitted",
                "organization_type": "center",
                "canonical_name": "共享研究中心",
            },
            pending[0]["id"]: {"choice": "keep-placement"},
        },
    )

    assert confirmed["summary"]["complete"] is True
    assert confirmed["organization_conflicts"][0]["acknowledged"] is True
    assert confirmed["organization_conflicts"][0]["requires_human_decision"] is False


def test_planned_cross_parent_same_name_requires_recorded_decisions(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "计算机中心老师",
                "cs-center@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/shared-center.html",
                department="共享研究中心",
            ),
            _row(
                "光电中心老师",
                "oe-center@example.edu",
                "示例大学",
                "光电学院",
                "https://oe.example.edu/teachers/shared-center.html",
                department="共享研究中心",
            ),
        ],
        number=767,
    )

    first = _plan(manifest, manifest_path, issue_number=767)
    pending = [item for item in first["questions"] if item["status"] == "pending"]

    assert first["decision"] is None
    assert first["summary"]["complete"] is False
    assert len(pending) == 2
    assert {item["type"] for item in pending} == {"same_name_different_parent"}
    assert all(item["context_recommendation"] is None for item in pending)

    keep_answers = {item["id"]: {"choice": "keep-placement"} for item in pending}
    kept = _plan(
        manifest,
        manifest_path,
        issue_number=767,
        answers=keep_answers,
    )

    assert kept["summary"]["complete"] is True
    assert len(kept["organization_conflicts"]) == 2
    assert all(item["acknowledged"] is True for item in kept["organization_conflicts"])
    assert all(
        item["requires_human_decision"] is False
        for item in kept["organization_conflicts"]
    )
    applied = _apply(root, 767, kept["decision"])
    assert applied.ready_for_finalization is True
    assert applied.created_organizations == 3


def test_planned_cross_parent_same_name_can_map_to_other_planned_organization(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "甲中心老师",
                "planned-a@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/planned-a.html",
                department="联合研究中心",
            ),
            _row(
                "乙中心老师",
                "planned-b@example.edu",
                "示例大学",
                "光电学院",
                "https://oe.example.edu/teachers/planned-b.html",
                department="联合研究中心",
            ),
        ],
        number=768,
    )
    first = _plan(manifest, manifest_path, issue_number=768)
    questions = first["questions"]

    with pytest.raises(AgentReviewError) as captured:
        _plan(
            manifest,
            manifest_path,
            issue_number=768,
            answers={
                item["id"]: {
                    "choice": "map-planned",
                    "organization_id": item["context"]["candidates"][0][
                        "organization_id"
                    ],
                }
                for item in questions
            },
        )
    assert captured.value.code == "review_answer_invalid"

    keep_question = next(item for item in questions if "光电学院" in item["path"])
    map_question = next(item for item in questions if "计算机学院" in item["path"])
    target_id = next(
        item["organization_id"]
        for item in map_question["context"]["candidates"]
        if "光电学院" in item["path"]
    )

    mapped = _plan(
        manifest,
        manifest_path,
        issue_number=768,
        answers={
            keep_question["id"]: {"choice": "keep-placement"},
            map_question["id"]: {
                "choice": "map-planned",
                "organization_id": target_id,
            },
        },
    )

    assert mapped["summary"]["complete"] is True
    assert mapped["organization_conflicts"] == []
    decisions = {
        item["group_id"]: item for item in mapped["decision"]["decisions"]
    }
    mapped_decision = next(
        item for item in decisions.values() if item["target_organization_id"] == target_id
    )
    assert mapped_decision["levels"] == []
    applied = _apply(root, 768, mapped["decision"])
    assert applied.ready_for_finalization is True
    assert applied.created_organizations == 2


def test_latest_main_organizations_override_stale_manifest_snapshot(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "最新机构老师",
                "latest@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/teachers/latest",
                department="智能科学系",
            )
        ],
        number=77,
    )
    latest_id = "org_latest_intelligence"
    latest = [
        {
            "id": latest_id,
            "type": "department",
            "canonical_name": "智能科学系",
            "parent_id": "org_example_cs",
            "aliases": [],
            "official_urls": [],
            "approved_domains": ["example.edu"],
            "lineage_ids": ["org_example_university", "org_example_cs", latest_id],
            "lineage_names": ["示例大学", "计算机学院", "智能科学系"],
        }
    ]

    draft = _plan(
        manifest,
        manifest_path,
        issue_number=77,
        latest_organizations=latest,
    )

    assert draft["summary"]["complete"] is True
    assert draft["decision"]["decisions"][0]["levels"][-1]["organization_id"] == latest_id


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
    assert result["duration_seconds"] >= 0
    assert {
        "sync",
        "prepare",
        "worktree",
        "materialize",
        "apply-review",
        "finalize-and-validate",
        "cleanup",
    }.issubset(result["stage_seconds"])
    assert result["main_sha"] == base_sha
    assert result["organization_changes"] == [
        {
            "action": "update",
            "id": "org_example_cs",
            "type": "school",
            "path": "示例大学 / 计算机学院",
            "aliases": ["计算机系", "计院"],
            "official_urls": ["https://cs.example.edu/"],
            "approved_domains": [],
            "status": "active",
            "successor_id": None,
            "changed_fields": {
                "aliases": {
                    "before": ["计算机系"],
                    "after": ["计算机系", "计院"],
                }
            },
        }
    ]
    assert not list(root.parent.glob("mentor-data-agent-review-*"))
