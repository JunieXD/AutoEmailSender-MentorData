from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mentor_data.batch import create_batch_proposals
from mentor_data.errors import SubmissionError
from mentor_data.github_events import GitHubActor, load_issue_event
from mentor_data.io_utils import load_json, load_yaml
from mentor_data.organization_review import (
    REVIEW_COMMENT_MARKER,
    _proposed_organization_id,
    apply_organization_review,
    create_organization_review_manifest,
    load_review_comment,
    load_review_pull,
)
from mentor_data.uploads import SAFE_COLUMNS

from .helpers import build_test_repository

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


def _decision(number: int, manifest_path: Path, decisions: list[dict]):
    return {
        "schema_version": 1,
        "kind": "batch_organization_review_decision",
        "pull_request_number": 88,
        "issue_number": number,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "decisions": decisions,
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
                "head": {"ref": "batch/issue-30-123", "repo": {"full_name": REPOSITORY}},
                "base": {"ref": "main"},
            }
        ),
        encoding="utf-8",
    )
    comment = load_review_comment(root, comment_path)
    pull = load_review_pull(pull_path, expected_repository=REPOSITORY, expected_number=88)
    return comment, pull


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
                profile_url="https://cs.example.edu/faculty/a",
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
    assert example_group["rows"][0]["profile_url"] == "https://cs.example.edu/faculty/a"
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
