from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import mentor_data.proposals as proposals_module
from mentor_data.batch import create_batch_proposals
from mentor_data.errors import SubmissionError
from mentor_data.github_events import GitHubActor, load_issue_event
from mentor_data.proposals import finalize_proposal_set
from mentor_data.repository import load_repository
from mentor_data.uploads import SAFE_COLUMNS

from .helpers import build_test_repository


def _batch_event(tmp_path):
    sections = {
        "社区共享包": (
            "[community.csv](https://github.com/user-attachments/assets/"
            "123e4567-e89b-12d3-a456-426614174000)"
        ),
        "补充说明": "示例大学计算机学院",
        "投稿确认": "- [x] 我确认文件只包含公开职业信息",
    }
    body = "\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items())
    event_path = tmp_path / "batch-event.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": 30,
                    "state": "open",
                    "html_url": "https://github.com/example/repository/issues/30",
                    "title": "[批量投稿] 示例",
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
    return load_issue_event(event_path, max_body_bytes=200_000)


def _package(tmp_path):
    path = tmp_path / "community.csv"
    row = [
        "示例导师",
        "mentor@example.edu",
        "教授",
        "示例大学",
        "计算机学院",
        "",
        "机器学习",
        "A Paper",
        "https://cs.example.edu/faculty/mentor",
        "https://cs.example.edu/faculty/mentor",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SAFE_COLUMNS)
        writer.writerow(row)
        writer.writerow(row)
    return path


def test_batch_duplicates_become_two_claims_for_one_mentor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = build_test_repository(tmp_path)
    actor = GitHubActor(
        user_id=7007,
        login="batch-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    result = create_batch_proposals(
        root,
        _batch_event(tmp_path),
        actor,
        package_path=_package(tmp_path),
        output_directory=tmp_path / "proposals",
    )
    assert len(result.paths) == 2
    assert result.proposals[0]["match_status"] == "new"
    assert result.proposals[1]["match_status"] == "matched_email"
    assert result.proposals[1]["target_mentor_id"] is not None

    original_load_repository = proposals_module.load_repository
    original_validator = proposals_module.Draft202012Validator
    load_count = 0
    validator_count = 0

    def counted_load_repository(*args, **kwargs):
        nonlocal load_count
        load_count += 1
        return original_load_repository(*args, **kwargs)

    def counted_validator(*args, **kwargs):
        nonlocal validator_count
        validator_count += 1
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(proposals_module, "load_repository", counted_load_repository)
    monkeypatch.setattr(proposals_module, "Draft202012Validator", counted_validator)
    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)
    assert load_count == 2
    assert validator_count == 1
    data = load_repository(root)
    assert len(data.mentors) == 1
    assert len(data.claims) == 2
    assert len(data.mentors[0]["claim_ids"]) == 2


def test_batch_same_resolved_organization_alias_is_not_an_affiliation_conflict(
    tmp_path,
) -> None:
    root = build_test_repository(tmp_path)
    package_path = tmp_path / "organization-alias.csv"
    first = [
        "示例导师",
        "mentor@example.edu",
        "教授",
        "示例大学",
        "计算机学院",
        "",
        "机器学习",
        "A Paper",
        "https://cs.example.edu/faculty/mentor",
        "https://cs.example.edu/faculty/mentor",
    ]
    second = first.copy()
    second[4] = "计算机系"
    with package_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SAFE_COLUMNS)
        writer.writerow(first)
        writer.writerow(second)

    result = create_batch_proposals(
        root,
        _batch_event(tmp_path),
        GitHubActor(
            user_id=7007,
            login="batch-user",
            user_type="User",
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        ),
        package_path=package_path,
        output_directory=tmp_path / "proposals",
    )

    assert result.proposals[1]["match_status"] == "matched_email"
    assert "email_organization_conflict" not in result.proposals[1]["review_reasons"]
    assert "identity_requires_manual_review" not in result.proposals[1]["review_reasons"]


def test_batch_unchecked_confirmation_is_rejected(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    event = _batch_event(tmp_path)
    event = replace(event, body=event.body.replace("- [x] 我确认", "- [ ] 我确认"))

    with pytest.raises(SubmissionError, match="批量投稿确认未完成"):
        create_batch_proposals(
            root,
            event,
            GitHubActor(
                user_id=7007,
                login="batch-user",
                user_type="User",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            ),
            package_path=_package(tmp_path),
            output_directory=tmp_path / "proposals",
        )


def test_batch_reuses_proposal_schema_and_match_indexes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = build_test_repository(tmp_path)
    actor = GitHubActor(
        user_id=7007,
        login="batch-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    original_load_json = proposals_module.load_json
    schema_load_count = 0

    def counted_load_json(path):
        nonlocal schema_load_count
        if path.name == "proposal.schema.json":
            schema_load_count += 1
        return original_load_json(path)

    monkeypatch.setattr(proposals_module, "load_json", counted_load_json)
    result = create_batch_proposals(
        root,
        _batch_event(tmp_path),
        actor,
        package_path=_package(tmp_path),
        output_directory=tmp_path / "proposals",
    )

    assert schema_load_count == 1
    assert result.proposals[0]["match_status"] == "new"
    assert result.proposals[1]["match_status"] == "matched_email"


def test_invalid_row_does_not_discard_other_batch_rows(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    package_path = tmp_path / "mixed.csv"
    valid = [
        "有效导师",
        "valid@example.edu",
        "教授",
        "示例大学",
        "计算机学院",
        "",
        "机器学习",
        "",
        "",
        "https://cs.example.edu/faculty/valid",
    ]
    invalid = valid.copy()
    invalid[1] = ""
    with package_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SAFE_COLUMNS)
        writer.writerow(invalid)
        writer.writerow(valid)
    actor = GitHubActor(
        user_id=7007,
        login="batch-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    result = create_batch_proposals(
        root,
        _batch_event(tmp_path),
        actor,
        package_path=package_path,
        output_directory=tmp_path / "proposals",
    )

    assert [item["issue"]["batch_row"] for item in result.proposals] == [2]
    assert result.invalid_rows[0]["batch_row"] == 1
    assert result.invalid_rows[0]["sheet_row"] == 2
    assert result.invalid_rows[0]["reason_code"] == "missing_required_fields"
    assert result.all_auto_eligible is False


def test_schema_invalid_row_is_isolated_instead_of_aborting_batch(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    package_path = tmp_path / "schema-invalid.csv"
    valid = [
        "有效导师",
        "valid@example.edu",
        "教授",
        "示例大学",
        "计算机学院",
        "",
        "机器学习",
        "A Paper",
        "http://cs.example.edu/faculty/valid",
        "http://cs.example.edu/faculty/valid",
    ]
    invalid = valid.copy()
    invalid[0] = "超长论文导师"
    invalid[1] = "long@example.edu"
    invalid[7] = "P" * 5_001
    with package_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SAFE_COLUMNS)
        writer.writerow(invalid)
        writer.writerow(valid)
    actor = GitHubActor(
        user_id=7007,
        login="batch-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    result = create_batch_proposals(
        root,
        _batch_event(tmp_path),
        actor,
        package_path=package_path,
        output_directory=tmp_path / "proposals",
    )

    assert [item["issue"]["batch_row"] for item in result.proposals] == [2]
    assert len(result.invalid_rows) == 1
    assert result.invalid_rows[0]["batch_row"] == 1
    assert len(result.invalid_rows[0]["message"]) <= 500
