from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mentor_data.batch import create_batch_proposals
from mentor_data.builder import build_dataset
from mentor_data.errors import SubmissionError
from mentor_data.github_events import GitHubActor, load_issue_event
from mentor_data.io_utils import load_json, load_yaml, write_yaml_atomic
from mentor_data.organization_review import (
    GITHUB_COMMENT_CHARACTER_LIMIT,
    REVIEW_COMMENT_MARKER,
    _parse_review_comment_payload,
    _proposed_organization_id,
    apply_organization_review,
    create_organization_review_manifest,
    load_review_comment,
    load_review_pull,
)
from mentor_data.proposals import finalize_proposal, finalize_proposal_set
from mentor_data.repository import load_repository
from mentor_data.uploads import SAFE_COLUMNS

from .helpers import build_test_repository, claim, fixed_datetime, mentor, save_claim, save_mentor

REPOSITORY = "example/repository"


def _event(tmp_path: Path, *, number: int = 30):
    body = "\n\n".join(
        [
            "### 社区共享包\n\n[community.csv](https://github.com/user-attachments/assets/123e4567-e89b-12d3-a456-426614174000)",
            "### 补充说明\n\n跨学校批量投稿",
            "### 投稿确认\n\n- [x] 我确认文件只包含公开职业信息",
        ]
    )
    path = tmp_path / f"issue-{number}.json"
    path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": number,
                    "state": "open",
                    "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
                    "title": "[批量投稿] 跨学校",
                    "body": body,
                    "created_at": "2026-08-03T00:00:00Z",
                    "user": {"id": 7007, "login": "batch-user", "type": "User"},
                    "labels": [{"name": "submission:batch"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return load_issue_event(path, max_body_bytes=200_000)


def _actor() -> GitHubActor:
    return GitHubActor(
        user_id=7007,
        login="batch-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


def _row(
    name: str,
    email: str,
    university: str,
    school: str,
    source_url: str,
    *,
    department: str = "",
    profile_url: str | None = None,
) -> list[str]:
    return [
        name,
        email,
        "教授",
        university,
        school,
        department,
        "机器学习",
        "A Paper",
        profile_url or source_url,
        source_url,
    ]


def _prepare(
    root: Path,
    tmp_path: Path,
    rows: list[list[str]],
    *,
    number: int = 30,
):
    package = tmp_path / f"package-{number}.csv"
    with package.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SAFE_COLUMNS)
        writer.writerows(rows)
    event = _event(tmp_path, number=number)
    proposal_directory = root / "proposals" / f"batch-issue-{number}"
    result = create_batch_proposals(
        root,
        event,
        _actor(),
        package_path=package,
        output_directory=proposal_directory,
    )
    manifest_path = root / "reviews" / "pending" / f"batch-issue-{number}.json"
    manifest = create_organization_review_manifest(
        root,
        event,
        result,
        proposal_directory=f"proposals/batch-issue-{number}",
        output_path=manifest_path,
        generated_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    return result, manifest, manifest_path


def _existing(level: str, organization_id: str, *, save_alias: bool = False):
    return {
        "level": level,
        "action": "existing",
        "organization_id": organization_id,
        "organization_type": None,
        "canonical_name": None,
        "official_url": None,
        "approved_domains": [],
        "save_submitted_as_alias": save_alias,
    }


def _create(
    level: str,
    organization_type: str,
    canonical_name: str,
    official_url: str | None,
    approved_domains: list[str],
    *,
    save_alias: bool,
):
    return {
        "level": level,
        "action": "create",
        "organization_id": None,
        "organization_type": organization_type,
        "canonical_name": canonical_name,
        "official_url": official_url,
        "approved_domains": approved_domains,
        "save_submitted_as_alias": save_alias,
    }


def _skip(level: str):
    return {
        "level": level,
        "action": "skip",
        "organization_id": None,
        "organization_type": None,
        "canonical_name": None,
        "official_url": None,
        "approved_domains": [],
        "save_submitted_as_alias": False,
    }


def _decision(
    number: int,
    manifest_path: Path,
    decisions: list[dict],
    *,
    organization_creations: list[dict] | None = None,
):
    payload = {
        "schema_version": 1,
        "kind": "batch_organization_review_decision",
        "pull_request_number": 88,
        "issue_number": number,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "decisions": decisions,
    }
    if organization_creations is not None:
        payload["organization_creations"] = organization_creations
    return payload


def _independent_creation(
    organization_type: str,
    canonical_name: str,
    parent_id: str | None,
    *,
    official_url: str | None = None,
    approved_domains: list[str] | None = None,
) -> dict:
    return {
        "organization_id": _proposed_organization_id(
            organization_type,
            canonical_name,
            parent_id,
        ),
        "organization_type": organization_type,
        "canonical_name": canonical_name,
        "parent_id": parent_id,
        "official_url": official_url,
        "approved_domains": approved_domains or [],
    }


def _review_context(
    root: Path,
    tmp_path: Path,
    decision: dict,
    *,
    association: str = "OWNER",
    line_ending: str = "\n",
):
    comment_path = tmp_path / "comment.json"
    comment_body = line_ending.join(
        [
            REVIEW_COMMENT_MARKER,
            "```json",
            json.dumps(decision, ensure_ascii=False),
            "```",
        ]
    )
    comment_path.write_text(
        json.dumps(
            {
                "issue": {"number": 88, "pull_request": {"url": "https://api.github.test/88"}},
                "comment": {
                    "id": 991,
                    "body": comment_body,
                    "created_at": "2026-08-03T01:00:00Z",
                    "author_association": association,
                    "user": {"id": 999, "login": "maintainer", "type": "User"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pull_path = tmp_path / "pull.json"
    pull_path.write_text(
        json.dumps(
            {
                "number": 88,
                "state": "open",
                "head": {
                    "ref": f"batch/issue-{decision['issue_number']}",
                    "repo": {"full_name": REPOSITORY},
                },
                "base": {"ref": "main"},
            }
        ),
        encoding="utf-8",
    )
    comment = load_review_comment(root, comment_path)
    pull = load_review_pull(pull_path, expected_repository=REPOSITORY, expected_number=88)
    return comment, pull


def _seed_existing_mentor(root: Path) -> None:
    initial_claim = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=1,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
        profile_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, initial_claim)
    save_mentor(root, mentor())


def _sample_affiliation_decision(
    group: dict,
    *,
    identity_resolutions: list[dict],
    row_overrides: list[dict] | None = None,
) -> dict:
    return {
        "group_id": group["id"],
        "action": "resolve",
        "reason": None,
        "levels": [
            _existing("university", "org_sample_university"),
            _existing("school", "org_sample_ai"),
            _skip("department"),
        ],
        "row_overrides": row_overrides or [],
        "identity_resolutions": identity_resolutions,
    }


def test_groups_cross_school_rows_and_saves_alias_once(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "甲老师",
                "a@example.edu",
                "示例大学",
                "计院",
                "https://cs.example.edu/a",
                profile_url="https://faculty.cs.example.edu/a",
            ),
            _row("乙老师", "b@example.edu", "示例大学", "计院", "https://cs.example.edu/b"),
            _row("丙老师", "c@sample.edu", "样本大学", "AI研究院", "https://ai.sample.edu/c"),
        ],
    )
    assert len(result.proposals) == 3
    assert sorted(len(group["rows"]) for group in manifest["groups"]) == [1, 2]
    example_group = next(
        group for group in manifest["groups"] if group["submitted"]["school"] == "计院"
    )
    sample_group = next(
        group for group in manifest["groups"] if group["submitted"]["school"] == "AI研究院"
    )
    assert example_group["rows"][0]["profile_url"] == "https://faculty.cs.example.edu/a"
    assert example_group["source_domains"] == ["cs.example.edu", "faculty.cs.example.edu"]
    assert example_group["source_urls"] == [
        "https://cs.example.edu/a",
        "https://cs.example.edu/b",
        "https://faculty.cs.example.edu/a",
    ]
    rejected_id = example_group["rows"][1]["proposal_id"]
    decisions = [
        {
            "group_id": example_group["id"],
            "action": "resolve",
            "reason": None,
            "levels": [
                _existing("university", "org_example_university"),
                _existing("school", "org_example_cs", save_alias=True),
                _skip("department"),
            ],
            "row_overrides": [
                {
                    "proposal_id": rejected_id,
                    "action": "reject",
                    "organization_id": None,
                    "reason": "来源不足",
                }
            ],
        },
        {
            "group_id": sample_group["id"],
            "action": "resolve",
            "reason": None,
            "levels": [
                _existing("university", "org_sample_university"),
                _existing("school", "org_sample_ai"),
                _skip("department"),
            ],
            "row_overrides": [],
        },
    ]
    comment, pull = _review_context(root, tmp_path, _decision(30, manifest_path, decisions))

    applied = apply_organization_review(root, comment, pull)

    assert applied.mapped_proposals == 2
    assert applied.rejected_proposals == 1
    assert applied.ready_for_finalization is True
    registry = load_yaml(root / "registry" / "organizations.yml")
    example_school = next(
        item for item in registry["organizations"] if item["id"] == "org_example_cs"
    )
    assert example_school["aliases"].count("计院") == 1
    first = load_json(root / "proposals" / "batch-issue-30" / "issue-30-row-0001.json")
    assert first["submitted"]["submitted_school"] == "计院"
    assert first["accepted"]["organization_id"] == "org_example_cs"
    assert not (root / "proposals" / "batch-issue-30" / "issue-30-row-0002.json").exists()
    resolution = load_json(root / "reviews" / "resolutions" / "batch-issue-30.json")
    assert resolution["reviewer"]["github_user_id"] == 999
    assert resolution["rejected_proposal_ids"] == [rejected_id]


def test_manifest_suggests_department_ending_in_school_as_sibling_school(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, _ = _prepare(
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
        number=35,
    )

    suggestion = manifest["groups"][0]["suggested_path_correction"]

    assert suggestion == {
        "kind": "department_as_school",
        "target_organization_id": None,
        "source": "heuristic",
        "reason": "系所字段以“学院”结尾，疑似同校平级学院",
    }


def test_manifest_does_not_suggest_sibling_when_school_and_department_are_same(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, _ = _prepare(
        root,
        tmp_path,
        [
            _row(
                "同层老师",
                "same@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/same",
                department="计算机学院",
            )
        ],
        number=36,
    )

    assert manifest["groups"][0]["suggested_path_correction"] is None


def test_manifest_suggests_department_ending_in_institute_as_sibling_institute(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, _ = _prepare(
        root,
        tmp_path,
        [
            _row(
                "研究院老师",
                "institute@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/institute",
                department="智能科学研究院",
            )
        ],
        number=41,
    )

    assert manifest["groups"][0]["suggested_path_correction"] == {
        "kind": "department_as_institute",
        "target_organization_id": None,
        "source": "heuristic",
        "reason": "系所字段以“研究院”结尾，疑似同校平级研究院",
    }


def test_review_creates_sibling_school_and_reuses_saved_path_correction(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
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
        number=37,
    )
    group = manifest["groups"][0]
    creation = _independent_creation(
        "school",
        "人工智能学院",
        "org_example_university",
    )
    target_id = creation["organization_id"]
    decision = _decision(
        37,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _existing("university", "org_example_university"),
                    _skip("school"),
                    _skip("department"),
                ],
                "target_organization_id": target_id,
                "mapping_kind": "department_as_school",
                "mapping_reason": "官网显示该导师属于同校人工智能学院。",
                "save_path_correction": True,
                "row_overrides": [],
                "identity_resolutions": [],
            }
        ],
        organization_creations=[creation],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    applied = apply_organization_review(root, comment, pull)

    assert applied.created_organizations == 1
    proposal = load_json(result.paths[0])
    assert proposal["accepted"]["organization_id"] == target_id
    registry = load_yaml(root / "registry" / "organizations.yml")
    created = next(item for item in registry["organizations"] if item["id"] == target_id)
    assert created["type"] == "school"
    assert created["parent_id"] == "org_example_university"
    assert "人工智能学院" not in next(
        item
        for item in registry["organizations"]
        if item["id"] == "org_example_cs"
    )["aliases"]
    resolution = load_json(root / "reviews" / "resolutions" / "batch-issue-37.json")
    assert resolution["path_corrections"] == [
        {
            "submitted": {
                "university": "示例大学",
                "school": "计算机学院",
                "department": "人工智能学院",
            },
            "target_organization_id": target_id,
            "kind": "department_as_school",
            "reason": "官网显示该导师属于同校人工智能学院。",
            "proposal_ids": [group["rows"][0]["proposal_id"]],
            "source_domains": ["cs.example.edu"],
        }
    ]
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)

    second_result, second_manifest, second_manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "后续老师",
                "later@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/later",
                department="人工智能学院",
            )
        ],
        number=38,
    )
    second_group = second_manifest["groups"][0]
    assert second_group["suggested_path_correction"] == {
        "kind": "department_as_school",
        "target_organization_id": target_id,
        "source": "history",
        "reason": "官网显示该导师属于同校人工智能学院。",
    }
    second_decision = _decision(
        38,
        second_manifest_path,
        [
            {
                "group_id": second_group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": target_id,
                "mapping_kind": "department_as_school",
                "mapping_reason": "沿用此前审核确认的完整路径纠错。",
                "save_path_correction": True,
                "row_overrides": [],
                "identity_resolutions": [],
            }
        ],
        organization_creations=[],
    )
    second_comment, second_pull = _review_context(root, tmp_path, second_decision)

    second_applied = apply_organization_review(root, second_comment, second_pull)

    assert second_applied.created_organizations == 0
    assert load_json(second_result.paths[0])["accepted"]["organization_id"] == target_id


def test_independent_school_can_be_used_only_for_a_row_override(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "甲老师",
                "a@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/a",
                department="人工智能学院",
            ),
            _row(
                "乙老师",
                "b@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/b",
                department="人工智能学院",
            ),
        ],
        number=39,
    )
    group = manifest["groups"][0]
    creation = _independent_creation(
        "school",
        "人工智能学院",
        "org_example_university",
    )
    target_id = creation["organization_id"]
    overridden_id = group["rows"][1]["proposal_id"]
    decision = _decision(
        39,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _existing("university", "org_example_university"),
                    _existing("school", "org_example_cs"),
                    _skip("department"),
                ],
                "row_overrides": [
                    {
                        "proposal_id": overridden_id,
                        "action": "map_existing",
                        "organization_id": target_id,
                        "reason": None,
                    }
                ],
                "identity_resolutions": [],
            }
        ],
        organization_creations=[creation],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    apply_organization_review(root, comment, pull)

    proposals = [load_json(path) for path in result.paths]
    assert {item["id"]: item["accepted"]["organization_id"] for item in proposals} == {
        group["rows"][0]["proposal_id"]: "org_example_cs",
        overridden_id: target_id,
    }
    resolution = load_json(root / "reviews" / "resolutions" / "batch-issue-39.json")
    assert resolution["path_corrections"] == []


def test_review_rejects_unused_or_forged_independent_organizations(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "安全老师",
                "safe@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/safe",
            )
        ],
        number=40,
    )
    group = manifest["groups"][0]
    base_group_decision = {
        "group_id": group["id"],
        "action": "resolve",
        "reason": None,
        "levels": [
            _existing("university", "org_example_university"),
            _existing("school", "org_example_cs"),
            _skip("department"),
        ],
        "row_overrides": [],
        "identity_resolutions": [],
    }
    unused = _independent_creation(
        "school",
        "未使用学院",
        "org_example_university",
    )
    unused_decision = _decision(
        40,
        manifest_path,
        [base_group_decision],
        organization_creations=[unused],
    )
    unused_comment, unused_pull = _review_context(root, tmp_path, unused_decision)

    with pytest.raises(SubmissionError, match="没有被任何导师使用"):
        apply_organization_review(root, unused_comment, unused_pull)

    forged = _independent_creation(
        "school",
        "伪造学院",
        "org_example_university",
    )
    forged["organization_id"] = "org_forged_school"
    forged_group = {
        **base_group_decision,
        "target_organization_id": "org_forged_school",
        "mapping_kind": "custom",
        "mapping_reason": "测试伪造 ID。",
        "save_path_correction": False,
    }
    forged_decision = _decision(
        40,
        manifest_path,
        [forged_group],
        organization_creations=[forged],
    )
    forged_comment, forged_pull = _review_context(root, tmp_path, forged_decision)

    with pytest.raises(SubmissionError, match="ID 与规范字段不一致"):
        apply_organization_review(root, forged_comment, forged_pull)

    blank_name = _independent_creation("school", " ", "org_example_university")
    blank_group = {
        **base_group_decision,
        "target_organization_id": blank_name["organization_id"],
        "mapping_kind": "custom",
        "mapping_reason": "测试空白名称。",
        "save_path_correction": False,
    }
    blank_decision = _decision(
        40,
        manifest_path,
        [blank_group],
        organization_creations=[blank_name],
    )
    blank_comment, blank_pull = _review_context(root, tmp_path, blank_decision)

    with pytest.raises(SubmissionError, match="必须填写正式名称"):
        apply_organization_review(root, blank_comment, blank_pull)


def test_historical_path_correction_follows_merged_organization_successor(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "历史老师",
                "history@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/history",
                department="人工智能学院",
            )
        ],
        number=42,
    )
    group = manifest["groups"][0]
    creation = _independent_creation(
        "school",
        "人工智能学院",
        "org_example_university",
    )
    original_target = creation["organization_id"]
    decision = _decision(
        42,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": original_target,
                "mapping_kind": "department_as_school",
                "mapping_reason": "官网确认该导师属于平级学院。",
                "save_path_correction": True,
                "row_overrides": [],
                "identity_resolutions": [],
            }
        ],
        organization_creations=[creation],
    )
    comment, pull = _review_context(root, tmp_path, decision)
    apply_organization_review(root, comment, pull)
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)

    organizations_document = load_yaml(root / "registry" / "organizations.yml")
    successor_id = "org_example_intelligence_school"
    original = next(
        item
        for item in organizations_document["organizations"]
        if item["id"] == original_target
    )
    original["status"] = "merged"
    original["successor_id"] = successor_id
    original["updated_at"] = "2026-08-04T00:00:00Z"
    organizations_document["organizations"].append(
        {
            "id": successor_id,
            "type": "school",
            "canonical_name": "智能科学学院",
            "parent_id": "org_example_university",
            "aliases": [],
            "official_urls": [],
            "approved_domains": [],
            "status": "active",
            "successor_id": None,
            "created_at": "2026-08-04T00:00:00Z",
            "updated_at": "2026-08-04T00:00:00Z",
        }
    )
    write_yaml_atomic(root / "registry" / "organizations.yml", organizations_document)
    load_repository(root, validate=True)

    _, next_manifest, _ = _prepare(
        root,
        tmp_path,
        [
            _row(
                "后续老师",
                "successor@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/successor",
                department="人工智能学院",
            )
        ],
        number=43,
    )

    assert next_manifest["groups"][0]["suggested_path_correction"] == {
        "kind": "department_as_school",
        "target_organization_id": successor_id,
        "source": "history",
        "reason": "官网确认该导师属于平级学院。",
    }


def test_review_rejects_invalid_path_correction_combinations(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "决策老师",
                "decision@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/decision",
            )
        ],
        number=44,
    )
    group = manifest["groups"][0]
    cases = [
        (
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "row_overrides": [],
                "identity_resolutions": [],
            },
            "没有机构层级或独立最终机构",
        ),
        (
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": "org_example_cs",
                "mapping_kind": "custom",
                "mapping_reason": None,
                "save_path_correction": False,
                "row_overrides": [],
                "identity_resolutions": [],
            },
            "必须填写审核依据",
        ),
        (
            {
                "group_id": group["id"],
                "action": "reject",
                "reason": "来源不足。",
                "levels": [],
                "target_organization_id": None,
                "mapping_kind": "custom",
                "mapping_reason": "不应保存。",
                "save_path_correction": True,
                "row_overrides": [],
                "identity_resolutions": [],
            },
            "不能保存路径纠错",
        ),
    ]
    for group_decision, expected_error in cases:
        decision = _decision(44, manifest_path, [group_decision])
        comment, pull = _review_context(root, tmp_path, decision)
        with pytest.raises(SubmissionError, match=expected_error):
            apply_organization_review(root, comment, pull)


def test_review_rejects_wrong_independent_parent_and_creation_without_mapped_mentor(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "安全边界老师",
                "edge@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/edge",
                department="边界学院",
            )
        ],
        number=45,
    )
    group = manifest["groups"][0]
    wrong_parent = _independent_creation("school", "边界学院", "org_example_cs")
    wrong_parent_decision = _decision(
        45,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": wrong_parent["organization_id"],
                "mapping_kind": "department_as_school",
                "mapping_reason": "测试错误上级。",
                "save_path_correction": False,
                "row_overrides": [],
                "identity_resolutions": [],
            }
        ],
        organization_creations=[wrong_parent],
    )
    comment, pull = _review_context(root, tmp_path, wrong_parent_decision)
    with pytest.raises(SubmissionError, match="上级机构类型不正确"):
        apply_organization_review(root, comment, pull)

    unused = _independent_creation("school", "边界学院", "org_example_university")
    proposal_id = group["rows"][0]["proposal_id"]
    unused_decision = _decision(
        45,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": unused["organization_id"],
                "mapping_kind": "department_as_school",
                "mapping_reason": "所有导师都拒绝时不应创建。",
                "save_path_correction": True,
                "row_overrides": [
                    {
                        "proposal_id": proposal_id,
                        "action": "reject",
                        "organization_id": None,
                        "reason": "该导师证据不足。",
                    }
                ],
                "identity_resolutions": [],
            }
        ],
        organization_creations=[unused],
    )
    comment, pull = _review_context(root, tmp_path, unused_decision)
    with pytest.raises(SubmissionError, match="没有被任何导师使用"):
        apply_organization_review(root, comment, pull)


def test_review_validates_source_domains_against_each_mentor_final_target(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "域名老师",
                "domain@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/domain",
            )
        ],
        number=46,
    )
    group = manifest["groups"][0]
    decision = _decision(
        46,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": "org_sample_ai",
                "mapping_kind": "custom",
                "mapping_reason": "故意选择不匹配的学院。",
                "save_path_correction": False,
                "row_overrides": [],
                "identity_resolutions": [],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    with pytest.raises(SubmissionError, match="官方来源不属于所选机构"):
        apply_organization_review(root, comment, pull)


def test_multiple_groups_reuse_one_independent_school_without_duplicate_creation(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "复用甲老师",
                "reuse-a@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/reuse-a",
                department="智能学院",
            ),
            _row(
                "复用乙老师",
                "reuse-b@example.edu",
                "示例大学",
                "电子学院",
                "https://faculty.example.edu/reuse-b",
                department="智能学院",
            ),
        ],
        number=47,
    )
    creation = _independent_creation("school", "智能学院", "org_example_university")
    target_id = creation["organization_id"]
    decisions = [
        {
            "group_id": group["id"],
            "action": "resolve",
            "reason": None,
            "levels": [],
            "target_organization_id": target_id,
            "mapping_kind": "department_as_school",
            "mapping_reason": "官网显示属于同一平级学院。",
            "save_path_correction": True,
            "row_overrides": [],
            "identity_resolutions": [],
        }
        for group in manifest["groups"]
    ]
    comment, pull = _review_context(
        root,
        tmp_path,
        _decision(47, manifest_path, decisions, organization_creations=[creation]),
    )

    applied = apply_organization_review(root, comment, pull)

    assert applied.created_organizations == 1
    assert {
        load_json(path)["accepted"]["organization_id"]
        for path in result.paths
    } == {target_id}
    resolution = load_json(root / "reviews" / "resolutions" / "batch-issue-47.json")
    assert resolution["organization_creations"] == [creation]
    assert len(resolution["path_corrections"]) == 2


def test_corrected_group_target_drives_existing_mentor_affiliation_resolution(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/mentor-ai",
                department="人工智能学院",
            )
        ],
        number=48,
    )
    group = manifest["groups"][0]
    proposal_id = group["rows"][0]["proposal_id"]
    assert group["rows"][0]["identity"]["requires_resolution"] is True
    creation = _independent_creation(
        "school",
        "人工智能学院",
        "org_example_university",
    )
    target_id = creation["organization_id"]
    decision = _decision(
        48,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [],
                "target_organization_id": target_id,
                "mapping_kind": "department_as_school",
                "mapping_reason": "官网显示导师已任职于平级人工智能学院。",
                "save_path_correction": True,
                "row_overrides": [],
                "identity_resolutions": [
                    {
                        "proposal_id": proposal_id,
                        "action": "append_current_affiliation",
                        "make_primary": False,
                        "former_affiliation_id": None,
                        "reason": "官网保留原学院职位并列出新学院任职。",
                    }
                ],
            }
        ],
        organization_creations=[creation],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    apply_organization_review(root, comment, pull)

    proposal = load_json(result.paths[0])
    assert proposal["accepted"]["organization_id"] == target_id
    assert proposal["affiliation_resolution"] == {
        "action": "append_current_affiliation",
        "organization_id": target_id,
        "make_primary": False,
        "former_affiliation_id": None,
        "reason": "官网保留原学院职位并列出新学院任职。",
    }


def test_manifest_shows_existing_mentor_and_current_affiliations_for_safe_conflict(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    _, manifest, _ = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor",
            )
        ],
    )

    row = manifest["groups"][0]["rows"][0]

    assert row["identity"] == {
        "requires_resolution": True,
        "target_mentor_id": "mentor_fixture_0001",
        "match_status": "conflict",
        "review_reasons": ["email_organization_conflict", "identity_requires_manual_review"],
        "mentor": {
            "id": "mentor_fixture_0001",
            "name": "示例导师",
            "email": "mentor@example.edu",
            "affiliations": [
                {
                    "id": "aff_fixture_primary",
                    "organization_id": "org_example_cs",
                    "status": "current",
                    "is_primary": True,
                    "title": "教授",
                    "source_url": "https://cs.example.edu/faculty/mentor",
                    "observed_at": "2026-08-03T00:00:00Z",
                }
            ],
        },
    }


def test_review_and_finalization_append_a_dual_appointment_without_duplicate_mentor(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor",
            )
        ],
    )
    group = manifest["groups"][0]
    proposal_id = group["rows"][0]["proposal_id"]
    decision = _decision(
        30,
        manifest_path,
        [
            _sample_affiliation_decision(
                group,
                identity_resolutions=[
                    {
                        "proposal_id": proposal_id,
                        "action": "append_current_affiliation",
                        "make_primary": False,
                        "former_affiliation_id": None,
                        "reason": "官网同时列在两个学院的导师页。",
                    }
                ],
            )
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    applied = apply_organization_review(root, comment, pull)

    assert applied.ready_for_finalization is True
    proposal_path = root / "proposals" / "batch-issue-30" / "issue-30-row-0001.json"
    proposal = load_json(proposal_path)
    assert proposal["affiliation_resolution"] == {
        "action": "append_current_affiliation",
        "organization_id": "org_sample_ai",
        "make_primary": False,
        "former_affiliation_id": None,
        "reason": "官网同时列在两个学院的导师页。",
    }

    _, mentor_path = finalize_proposal(root, proposal_path, moderator_github_user_id=999)

    finalized = load_json(mentor_path)
    assert len(load_repository(root).mentors) == 1
    current_affiliations = [
        affiliation
        for affiliation in finalized["affiliations"]
        if affiliation["status"] == "current"
    ]
    assert {item["organization_id"] for item in current_affiliations} == {
        "org_example_cs",
        "org_sample_ai",
    }
    assert sum(item["is_primary"] for item in current_affiliations) == 1
    primary_affiliation = next(item for item in current_affiliations if item["is_primary"])
    assert primary_affiliation["organization_id"] == "org_example_cs"
    assert finalized["contacts"][0]["affiliation_id"] == "aff_fixture_primary"


def test_same_new_mentor_in_two_organizations_finalizes_as_dual_appointment(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "新导师",
                "new@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/new",
            ),
            _row(
                "新导师",
                "new@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/new",
            ),
        ],
    )
    example_group = next(
        group
        for group in manifest["groups"]
        if group["submitted"]["school"] == "计算机学院"
    )
    sample_group = next(
        group
        for group in manifest["groups"]
        if group["submitted"]["school"] == "AI研究院"
    )
    second_id = sample_group["rows"][0]["proposal_id"]
    assert sample_group["rows"][0]["identity"]["target_mentor_id"] == result.proposals[1][
        "target_mentor_id"
    ]
    decisions = [
        {
            "group_id": example_group["id"],
            "action": "resolve",
            "reason": None,
            "levels": [
                _existing("university", "org_example_university"),
                _existing("school", "org_example_cs"),
                _skip("department"),
            ],
            "row_overrides": [],
            "identity_resolutions": [],
        },
        _sample_affiliation_decision(
            sample_group,
            identity_resolutions=[
                {
                    "proposal_id": second_id,
                    "action": "append_current_affiliation",
                    "make_primary": False,
                    "former_affiliation_id": None,
                    "reason": "两个学院官网均列出该导师。",
                }
            ],
        ),
    ]
    comment, pull = _review_context(root, tmp_path, _decision(30, manifest_path, decisions))

    applied = apply_organization_review(root, comment, pull)

    assert applied.ready_for_finalization is True
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)
    data = load_repository(root)
    assert len(data.mentors) == 1
    assert {
        item["organization_id"]
        for item in data.mentors[0]["affiliations"]
        if item["status"] == "current"
    } == {"org_example_cs", "org_sample_ai"}


def test_rejecting_first_new_mentor_row_promotes_the_next_accepted_row(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "新导师",
                "new@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/new",
            ),
            _row(
                "新导师",
                "new@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/new",
            ),
        ],
        number=34,
    )
    first_group = next(
        group
        for group in manifest["groups"]
        if group["submitted"]["school"] == "计算机学院"
    )
    second_group = next(
        group for group in manifest["groups"] if group["submitted"]["school"] == "AI研究院"
    )
    second_proposal_id = second_group["rows"][0]["proposal_id"]
    decisions = [
        {
            "group_id": first_group["id"],
            "action": "reject",
            "reason": "首行证据不足。",
            "levels": [],
            "row_overrides": [],
            "identity_resolutions": [],
        },
        _sample_affiliation_decision(
            second_group,
            identity_resolutions=[
                {
                    "proposal_id": second_proposal_id,
                    "action": "append_current_affiliation",
                    "make_primary": False,
                    "former_affiliation_id": None,
                    "reason": "第二行官网证据有效。",
                }
            ],
        ),
    ]
    comment, pull = _review_context(
        root,
        tmp_path,
        _decision(34, manifest_path, decisions),
    )

    applied = apply_organization_review(root, comment, pull)

    assert applied.rejected_proposals == 1
    assert applied.ready_for_finalization is True
    remaining_paths = [path for path in result.paths if path.exists()]
    promoted = load_json(remaining_paths[0])
    assert promoted["target_mentor_id"] is None
    assert promoted["match_status"] == "new"
    assert "affiliation_resolution" not in promoted
    finalize_proposal_set(root, remaining_paths, moderator_github_user_id=999)
    finalized = load_repository(root).mentors
    assert len(finalized) == 1
    assert finalized[0]["affiliations"][0]["organization_id"] == "org_sample_ai"


def test_same_new_mentor_with_two_unknown_organizations_is_order_independent(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "新导师",
                "new@shared.edu",
                "新甲大学",
                "甲学院",
                "https://first-new.edu/faculty/new",
            ),
            _row(
                "新导师",
                "new@shared.edu",
                "新乙大学",
                "乙学院",
                "https://second-new.edu/faculty/new",
            ),
        ],
        number=31,
    )
    first_group = next(
        group for group in manifest["groups"] if group["submitted"]["school"] == "甲学院"
    )
    second_group = next(
        group for group in manifest["groups"] if group["submitted"]["school"] == "乙学院"
    )
    identity = second_group["rows"][0]["identity"]
    assert identity["mentor"]["affiliations"][0]["organization_id"] is None
    assert identity["mentor"]["affiliations"][0]["organization_label"] == "新甲大学 / 甲学院"
    second_id = second_group["rows"][0]["proposal_id"]
    decisions = [
        {
            "group_id": first_group["id"],
            "action": "resolve",
            "reason": None,
            "levels": [
                _create(
                    "university",
                    "university",
                    "新甲大学",
                    "https://first-new.edu/",
                    ["first-new.edu"],
                    save_alias=True,
                ),
                _create("school", "school", "甲学院", None, [], save_alias=True),
                _skip("department"),
            ],
            "row_overrides": [],
            "identity_resolutions": [],
        },
        {
            "group_id": second_group["id"],
            "action": "resolve",
            "reason": None,
            "levels": [
                _create(
                    "university",
                    "university",
                    "新乙大学",
                    "https://second-new.edu/",
                    ["second-new.edu"],
                    save_alias=True,
                ),
                _create("school", "school", "乙学院", None, [], save_alias=True),
                _skip("department"),
            ],
            "row_overrides": [],
            "identity_resolutions": [
                {
                    "proposal_id": second_id,
                    "action": "append_current_affiliation",
                    "make_primary": False,
                    "former_affiliation_id": None,
                    "reason": "两个新学院官网均列出该导师。",
                }
            ],
        },
    ]
    comment, pull = _review_context(
        root,
        tmp_path,
        _decision(31, manifest_path, decisions),
    )

    applied = apply_organization_review(root, comment, pull)

    assert applied.ready_for_finalization is True
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)
    finalized = load_repository(root).mentors[0]
    assert len(
        [item for item in finalized["affiliations"] if item["status"] == "current"]
    ) == 2


def test_same_new_mentor_unknown_aliases_can_resolve_to_one_new_organization(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "新导师",
                "new@new.edu",
                "新示例大学",
                "甲学院",
                "https://new.edu/faculty/new",
            ),
            _row(
                "新导师",
                "new@new.edu",
                "新示例大学",
                "甲系",
                "https://new.edu/faculty/new",
            ),
        ],
        number=32,
    )
    decisions = []
    for group in manifest["groups"]:
        decisions.append(
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _create(
                        "university",
                        "university",
                        "新示例大学",
                        "https://new.edu/",
                        ["new.edu"],
                        save_alias=True,
                    ),
                    _create(
                        "school",
                        "school",
                        "统一学院",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": [],
                "identity_resolutions": [],
            }
        )
    comment, pull = _review_context(
        root,
        tmp_path,
        _decision(32, manifest_path, decisions),
    )

    applied = apply_organization_review(root, comment, pull)

    assert applied.ready_for_finalization is True
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)
    finalized = load_repository(root).mentors[0]
    current_affiliations = [
        item for item in finalized["affiliations"] if item["status"] == "current"
    ]
    assert len(current_affiliations) == 1


def test_dual_appointment_reusing_primary_profile_keeps_primary_projection(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    result, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "示例大学",
                "新人工智能学院",
                "https://cs.example.edu/faculty/mentor",
            )
        ],
        number=33,
    )
    group = manifest["groups"][0]
    proposal_id = group["rows"][0]["proposal_id"]
    decision = _decision(
        33,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _existing("university", "org_example_university"),
                    _create(
                        "school",
                        "school",
                        "新人工智能学院",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": [],
                "identity_resolutions": [
                    {
                        "proposal_id": proposal_id,
                        "action": "append_current_affiliation",
                        "make_primary": False,
                        "former_affiliation_id": None,
                        "reason": "同一校级主页同时证明双聘。",
                    }
                ],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)
    apply_organization_review(root, comment, pull)
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)

    finalized = load_repository(root).mentors[0]
    profile = next(
        item
        for item in finalized["profiles"]
        if item["url"] == "https://cs.example.edu/faculty/mentor"
    )
    assert profile["affiliation_id"] == "aff_fixture_primary"

    latest = build_dataset(root, tmp_path / "dist", generated_at=fixed_datetime())
    catalog = load_json(tmp_path / "dist" / latest["catalog_path"])
    unit_path = catalog["universities"][0]["units"][0]["path"]
    shard = load_json(tmp_path / "dist" / unit_path)
    assert shard["records"][0]["profile_url"] == profile["url"]


def test_review_and_finalization_transfer_affiliation_and_publish_new_primary_shard(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor",
            )
        ],
    )
    group = manifest["groups"][0]
    proposal_id = group["rows"][0]["proposal_id"]
    decision = _decision(
        30,
        manifest_path,
        [
            _sample_affiliation_decision(
                group,
                identity_resolutions=[
                    {
                        "proposal_id": proposal_id,
                        "action": "transfer_current_affiliation",
                        "make_primary": True,
                        "former_affiliation_id": "aff_fixture_primary",
                        "reason": "新学院官网显示该导师已调入本院。",
                    }
                ],
            )
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)
    apply_organization_review(root, comment, pull)
    proposal_path = root / "proposals" / "batch-issue-30" / "issue-30-row-0001.json"

    _, mentor_path = finalize_proposal(root, proposal_path, moderator_github_user_id=999)

    finalized = load_json(mentor_path)
    former = next(item for item in finalized["affiliations"] if item["id"] == "aff_fixture_primary")
    current = next(item for item in finalized["affiliations"] if item["status"] == "current")
    assert former["status"] == "former"
    assert former["is_primary"] is False
    assert former["ended_at"] == "2026-08-03"
    assert current["organization_id"] == "org_sample_ai"
    assert current["is_primary"] is True
    assert all(
        profile["status"] == "former"
        for profile in finalized["profiles"]
        if profile["affiliation_id"] == "aff_fixture_primary"
    )

    latest = build_dataset(root, tmp_path / "dist", generated_at=fixed_datetime())
    catalog = load_json(tmp_path / "dist" / latest["catalog_path"])
    sample_university = next(
        item for item in catalog["universities"] if item["name"] == "样本大学"
    )
    shard = load_json(tmp_path / "dist" / sample_university["units"][0]["path"])
    assert shard["records"][0]["school"] == "人工智能研究院"
    revocations = load_json(
        tmp_path
        / "dist"
        / latest["catalog_path"].replace("catalog.json", "revocations.json")
    )
    relocation = next(event for event in revocations["events"] if event.get("kind") == "relocation")
    assert relocation["community_record_id"] == "mentor_fixture_0001"
    assert relocation["status"] == "relocated"
    assert relocation["from_organization_id"] == "org_example_cs"
    assert relocation["to_organization_id"] == "org_sample_ai"
    transfer_claim = next(
        item
        for item in load_repository(root).claims
        if item["accepted"]["organization_id"] == "org_sample_ai"
    )
    assert relocation["observed_at"] == transfer_claim["moderation"]["decision_at"]
    assert "published_at" not in relocation


def test_review_requires_a_safe_affiliation_decision_for_a_new_current_organization(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor",
            )
        ],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [_sample_affiliation_decision(group, identity_resolutions=[])],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    with pytest.raises(SubmissionError, match="请选择新增双聘任职、已调动到新学院，或拒绝该导师"):
        apply_organization_review(root, comment, pull)


def test_review_rejects_invalid_or_stale_affiliation_decisions(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor",
            )
        ],
    )
    group = manifest["groups"][0]
    proposal_id = group["rows"][0]["proposal_id"]
    invalid_transfer = _decision(
        30,
        manifest_path,
        [
            _sample_affiliation_decision(
                group,
                identity_resolutions=[
                    {
                        "proposal_id": proposal_id,
                        "action": "transfer_current_affiliation",
                        "make_primary": True,
                        "former_affiliation_id": "aff_missing_0001",
                        "reason": "官网显示调动。",
                    }
                ],
            )
        ],
    )
    comment, pull = _review_context(root, tmp_path, invalid_transfer)

    with pytest.raises(SubmissionError, match="原当前任职不存在"):
        apply_organization_review(root, comment, pull)

    stale_resolution = _decision(
        30,
        manifest_path,
        [
            _sample_affiliation_decision(
                group,
                row_overrides=[
                    {
                        "proposal_id": proposal_id,
                        "action": "map_existing",
                        "organization_id": "org_example_cs",
                        "reason": None,
                    }
                ],
                identity_resolutions=[
                    {
                        "proposal_id": proposal_id,
                        "action": "append_current_affiliation",
                        "make_primary": False,
                        "former_affiliation_id": None,
                        "reason": "重复添加。",
                    }
                ],
            )
        ],
    )
    comment, pull = _review_context(root, tmp_path, stale_resolution)

    with pytest.raises(SubmissionError, match="已是导师当前任职"):
        apply_organization_review(root, comment, pull)


def test_review_rejects_affiliation_decision_for_a_row_without_safe_identity_match(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "新老师",
                "new@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/faculty/new",
            )
        ],
    )
    group = manifest["groups"][0]
    proposal_id = group["rows"][0]["proposal_id"]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _existing("university", "org_example_university"),
                    _existing("school", "org_example_cs"),
                    _skip("department"),
                ],
                "row_overrides": [],
                "identity_resolutions": [
                    {
                        "proposal_id": proposal_id,
                        "action": "append_current_affiliation",
                        "make_primary": False,
                        "former_affiliation_id": None,
                        "reason": "不应允许。",
                    }
                ],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    with pytest.raises(SubmissionError, match="不支持当前任职判定"):
        apply_organization_review(root, comment, pull)


def test_review_rejects_order_dependent_duplicate_affiliation_outcomes(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _seed_existing_mentor(root)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor-one",
            ),
            _row(
                "示例导师",
                "mentor@example.edu",
                "样本大学",
                "AI研究院",
                "https://ai.sample.edu/faculty/mentor-two",
            ),
        ],
    )
    group = manifest["groups"][0]
    first_id, second_id = [row["proposal_id"] for row in group["rows"]]
    decision = _decision(
        30,
        manifest_path,
        [
            _sample_affiliation_decision(
                group,
                identity_resolutions=[
                    {
                        "proposal_id": first_id,
                        "action": "append_current_affiliation",
                        "make_primary": False,
                        "former_affiliation_id": None,
                        "reason": "双聘证据。",
                    },
                    {
                        "proposal_id": second_id,
                        "action": "transfer_current_affiliation",
                        "make_primary": True,
                        "former_affiliation_id": "aff_fixture_primary",
                        "reason": "调动证据。",
                    },
                ],
            )
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    with pytest.raises(SubmissionError, match="多条任职判定互相冲突"):
        apply_organization_review(root, comment, pull)


def test_review_accepts_legacy_manifest_without_profile_url(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("甲老师", "a@example.edu", "示例大学", "计院", "https://cs.example.edu/a")],
    )
    group = manifest["groups"][0]
    group["rows"][0].pop("profile_url")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "reject",
                "reason": "兼容旧审核清单",
                "levels": [],
                "row_overrides": [],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    applied = apply_organization_review(root, comment, pull)

    assert applied.rejected_proposals == 1
    assert applied.remaining_proposals == 0


def test_review_can_create_new_university_and_school_chain(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("新老师", "mentor@new.edu", "新大", "工学院", "https://engineering.new.edu/mentor")],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _create(
                        "university",
                        "university",
                        "新示例大学",
                        "https://www.new.edu/",
                        ["new.edu"],
                        save_alias=True,
                    ),
                    _create(
                        "school",
                        "school",
                        "工学院",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": [],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    applied = apply_organization_review(root, comment, pull)

    assert applied.created_organizations == 2
    registry = load_yaml(root / "registry" / "organizations.yml")
    university = next(
        item for item in registry["organizations"] if item["canonical_name"] == "新示例大学"
    )
    school = next(item for item in registry["organizations"] if item["canonical_name"] == "工学院")
    assert university["aliases"] == ["新大"]
    assert school["parent_id"] == university["id"]
    assert school["official_urls"] == []
    proposal = load_json(root / "proposals" / "batch-issue-30" / "issue-30-row-0001.json")
    assert proposal["accepted"]["organization_id"] == school["id"]


def test_create_action_reuses_exact_existing_organization_chain(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row(
                "甲老师",
                "a@example.edu",
                "示例大学",
                "计算机学院",
                "https://cs.example.edu/a",
            )
        ],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _create(
                        "university",
                        "university",
                        "示例大学",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _create(
                        "school",
                        "school",
                        "计算机学院",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": [],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    applied = apply_organization_review(root, comment, pull)

    assert applied.created_organizations == 0
    proposal = load_json(root / "proposals" / "batch-issue-30" / "issue-30-row-0001.json")
    assert proposal["accepted"]["organization_id"] == "org_example_cs"


def test_review_uses_trusted_schemas_for_an_older_pull_request(tmp_path: Path) -> None:
    review_root = build_test_repository(tmp_path / "review")
    trusted_root = build_test_repository(tmp_path / "trusted")
    _, manifest, manifest_path = _prepare(
        review_root,
        tmp_path,
        [_row("新老师", "mentor@new.edu", "新大", "工学院", "https://new.edu/mentor")],
    )
    group = manifest["groups"][0]
    legacy_schema_path = review_root / "schemas" / "organization.schema.json"
    legacy_schema = load_json(legacy_schema_path)
    legacy_schema["$defs"]["organization"]["properties"]["official_urls"]["minItems"] = 1
    legacy_schema["$defs"]["organization"].pop("allOf", None)
    legacy_schema_path.write_text(
        json.dumps(legacy_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _create(
                        "university",
                        "university",
                        "新示例大学",
                        "https://new.edu/",
                        ["new.edu"],
                        save_alias=True,
                    ),
                    _create(
                        "school",
                        "school",
                        "工学院",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": [],
            }
        ],
    )
    comment, pull = _review_context(trusted_root, tmp_path, decision)

    applied = apply_organization_review(
        review_root,
        comment,
        pull,
        schema_root=trusted_root,
    )

    assert applied.ready_for_finalization is True
    registry = load_yaml(review_root / "registry" / "organizations.yml")
    school = next(item for item in registry["organizations"] if item["canonical_name"] == "工学院")
    assert school["official_urls"] == []


def test_review_requires_official_url_only_for_new_university(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("新老师", "mentor@new.edu", "新大", "工学院", "https://new.edu/mentor")],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _create(
                        "university",
                        "university",
                        "新示例大学",
                        None,
                        ["new.edu"],
                        save_alias=True,
                    ),
                    _create(
                        "school",
                        "school",
                        "工学院",
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": [],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    with pytest.raises(SubmissionError, match="新学校必须填写"):
        apply_organization_review(root, comment, pull)


def test_row_override_can_target_organization_created_by_later_group(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [
            _row("甲老师", "a@new.edu", "新示例大学", "甲学院", "https://new.edu/a"),
            _row("乙老师", "b@new.edu", "新示例大学", "乙学院", "https://new.edu/b"),
        ],
    )
    first_group, later_group = sorted(manifest["groups"], key=lambda item: item["id"])
    university_id = _proposed_organization_id("university", "新示例大学", None)
    later_school_name = later_group["submitted"]["school"]
    later_school_id = _proposed_organization_id("school", later_school_name, university_id)

    decisions = []
    for group in manifest["groups"]:
        row_overrides = []
        if group["id"] == first_group["id"]:
            row_overrides.append(
                {
                    "proposal_id": group["rows"][0]["proposal_id"],
                    "action": "map_existing",
                    "organization_id": later_school_id,
                    "reason": None,
                }
            )
        decisions.append(
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    _create(
                        "university",
                        "university",
                        "新示例大学",
                        "https://new.edu/",
                        ["new.edu"],
                        save_alias=True,
                    ),
                    _create(
                        "school",
                        "school",
                        group["submitted"]["school"],
                        None,
                        [],
                        save_alias=True,
                    ),
                    _skip("department"),
                ],
                "row_overrides": row_overrides,
            }
        )

    comment, pull = _review_context(root, tmp_path, _decision(30, manifest_path, decisions))
    applied = apply_organization_review(root, comment, pull)

    assert applied.created_organizations == 3
    first_proposal = load_json(
        root
        / "proposals"
        / "batch-issue-30"
        / f"issue-30-row-{first_group['rows'][0]['batch_row']:04d}.json"
    )
    assert first_proposal["accepted"]["organization_id"] == later_school_id


def test_review_rejects_unapproved_row_override_domain(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("甲老师", "a@example.edu", "示例大学", "计院", "https://cs.example.edu/a")],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "reject",
                "reason": "分组不可靠",
                "levels": [],
                "row_overrides": [
                    {
                        "proposal_id": group["rows"][0]["proposal_id"],
                        "action": "map_existing",
                        "organization_id": "org_sample_ai",
                        "reason": None,
                    }
                ],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)

    with pytest.raises(SubmissionError, match="批准域名"):
        apply_organization_review(root, comment, pull)


def test_review_comment_requires_trusted_repository_association(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("甲老师", "a@example.edu", "示例大学", "计算机学院", "https://cs.example.edu/a")],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "reject",
                "reason": "测试",
                "levels": [],
                "row_overrides": [],
            }
        ],
    )

    with pytest.raises(SubmissionError, match="受信任协作者"):
        _review_context(root, tmp_path, decision, association="NONE")


def test_review_comment_accepts_github_crlf_line_endings(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("甲老师", "a@example.edu", "示例大学", "计算机学院", "https://cs.example.edu/a")],
    )
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": manifest["groups"][0]["id"],
                "action": "reject",
                "reason": "测试",
                "levels": [],
                "row_overrides": [],
            }
        ],
    )

    comment, _ = _review_context(root, tmp_path, decision, line_ending="\r\n")

    assert comment.decision == decision


def test_review_comment_enforces_github_character_limit() -> None:
    prefix = f'{REVIEW_COMMENT_MARKER}\n```json\n{{"text":"'
    suffix = '"}\n```'
    body = prefix + "界" * (GITHUB_COMMENT_CHARACTER_LIMIT - len(prefix) - len(suffix)) + suffix

    assert len(body) == GITHUB_COMMENT_CHARACTER_LIMIT
    assert _parse_review_comment_payload(body)["text"]

    with pytest.raises(SubmissionError, match="65,536 字符上限"):
        _parse_review_comment_payload(body + " ")


def test_review_rejects_stale_manifest_digest(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    _, manifest, manifest_path = _prepare(
        root,
        tmp_path,
        [_row("甲老师", "a@example.edu", "示例大学", "计算机学院", "https://cs.example.edu/a")],
    )
    group = manifest["groups"][0]
    decision = _decision(
        30,
        manifest_path,
        [
            {
                "group_id": group["id"],
                "action": "reject",
                "reason": "测试",
                "levels": [],
                "row_overrides": [],
            }
        ],
    )
    comment, pull = _review_context(root, tmp_path, decision)
    manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(SubmissionError, match="已经变化"):
        apply_organization_review(root, comment, pull)


def test_review_pull_rejects_fork_branch(tmp_path: Path) -> None:
    pull_path = tmp_path / "fork-pull.json"
    pull_path.write_text(
        json.dumps(
            {
                "number": 88,
                "state": "open",
                "head": {
                    "ref": "batch/issue-30-123",
                    "repo": {"full_name": "attacker/fork"},
                },
                "base": {"ref": "main"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SubmissionError, match="fork"):
        load_review_pull(pull_path, expected_repository=REPOSITORY, expected_number=88)


def test_review_pull_rejects_legacy_run_specific_branch(tmp_path: Path) -> None:
    pull_path = tmp_path / "legacy-pull.json"
    pull_path.write_text(
        json.dumps(
            {
                "number": 88,
                "state": "open",
                "head": {
                    "ref": "batch/issue-30-123-2",
                    "repo": {"full_name": REPOSITORY},
                },
                "base": {"ref": "main"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SubmissionError, match="内部分支"):
        load_review_pull(pull_path, expected_repository=REPOSITORY, expected_number=88)
