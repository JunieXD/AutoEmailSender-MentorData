from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from mentor_data.errors import SubmissionError
from mentor_data.github_events import GitHubActor, load_issue_event
from mentor_data.proposals import create_mentor_proposal, finalize_proposal
from mentor_data.repository import load_repository

from .helpers import build_test_repository


def _issue_body(
    *,
    name: str = "示例导师",
    email: str = "mentor@example.edu",
    recent_papers: str = "A Safe Example Paper",
) -> str:
    sections = {
        "导师姓名": name,
        "公开工作邮箱": email,
        "社区机构 ID": "org_example_cs",
        "学校正式名称": "示例大学",
        "学院或研究院正式名称": "计算机学院",
        "系所或中心": "_No response_",
        "职称": "教授",
        "研究方向": "机器学习",
        "近期或代表论文": recent_papers,
        "官方个人主页": "https://cs.example.edu/faculty/mentor",
        "官方证据页面": "https://cs.example.edu/faculty/mentor",
        "投稿确认": "- [x] 我确认提交的是公开职业信息",
    }
    return "\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items())


def _write_event(tmp_path, *, issue_number: int, user_id: int, login: str, body: str):
    event_path = tmp_path / f"event-{issue_number}.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": issue_number,
                    "html_url": f"https://github.com/example/repository/issues/{issue_number}",
                    "title": "[导师投稿] 示例导师",
                    "body": body,
                    "created_at": "2026-08-03T00:00:00Z",
                    "user": {"id": user_id, "login": login, "type": "User"},
                    "labels": [{"name": "submission:mentor"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return event_path


def _actor(user_id: int, login: str, *, created_year: int = 2020) -> GitHubActor:
    return GitHubActor(
        user_id=user_id,
        login=login,
        user_type="User",
        created_at=datetime(created_year, 1, 1, tzinfo=UTC),
    )


def test_new_submission_creates_reviewable_proposal_and_finalizes(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    event_path = _write_event(
        tmp_path,
        issue_number=10,
        user_id=1001,
        login="fixture-one",
        body=_issue_body(),
    )
    event = load_issue_event(event_path, max_body_bytes=200_000)
    result = create_mentor_proposal(
        root,
        event,
        _actor(1001, "fixture-one"),
        output_directory=tmp_path / "proposals",
    )
    assert result.proposal["match_status"] == "new"
    assert result.proposal["auto_eligible"] is False
    assert "auto_merge_disabled" in result.proposal["review_reasons"]

    claim_path, mentor_path = finalize_proposal(
        root,
        result.path,
        moderator_github_user_id=999,
    )
    assert claim_path.exists()
    assert mentor_path.exists()
    data = load_repository(root)
    assert len(data.mentors) == 1
    assert data.mentors[0]["contacts"][0]["normalized_value"] == "mentor@example.edu"

    repeated_claim_path, repeated_mentor_path = finalize_proposal(
        root,
        result.path,
        moderator_github_user_id=999,
    )
    assert repeated_claim_path == claim_path
    assert repeated_mentor_path == mentor_path
    repeated_data = load_repository(root)
    assert len(repeated_data.claims) == 1
    assert len(repeated_data.mentors) == 1


def test_new_single_form_without_internal_organization_field_is_accepted(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    body = _issue_body().replace(
        "### 社区机构 ID\n\norg_example_cs\n\n",
        "",
    )
    event_path = _write_event(
        tmp_path,
        issue_number=13,
        user_id=3003,
        login="software-prefill-user",
        body=body,
    )

    result = create_mentor_proposal(
        root,
        load_issue_event(event_path, max_body_bytes=200_000),
        _actor(3003, "software-prefill-user"),
        output_directory=tmp_path / "proposals",
    )

    assert result.proposal["submitted"]["organization_id"] == "org_example_cs"


def test_http_official_urls_are_accepted_and_preserved(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    event_path = _write_event(
        tmp_path,
        issue_number=14,
        user_id=5005,
        login="http-source-user",
        body=_issue_body().replace("https://cs.example.edu", "http://cs.example.edu"),
    )
    result = create_mentor_proposal(
        root,
        load_issue_event(event_path, max_body_bytes=200_000),
        _actor(5005, "http-source-user"),
        output_directory=tmp_path / "proposals",
    )

    assert result.proposal["submitted"]["profile_url"].startswith("http://")
    assert result.proposal["submitted"]["source_url"].startswith("http://")
    finalize_proposal(root, result.path, moderator_github_user_id=999)
    mentor = load_repository(root).mentors[0]
    assert mentor["profiles"][0]["url"].startswith("http://")
    assert mentor["contacts"][0]["source_url"].startswith("http://")


def test_realistic_long_publication_summary_is_accepted(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    event_path = _write_event(
        tmp_path,
        issue_number=15,
        user_id=6006,
        login="long-publication-user",
        body=_issue_body(recent_papers="P" * 3_405),
    )

    result = create_mentor_proposal(
        root,
        load_issue_event(event_path, max_body_bytes=200_000),
        _actor(6006, "long-publication-user"),
        output_directory=tmp_path / "proposals",
    )

    assert len(result.proposal["submitted"]["recent_papers"][0]) == 3_405


def test_independent_duplicate_submission_adds_provenance_not_a_second_mentor(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    first_event_path = _write_event(
        tmp_path,
        issue_number=10,
        user_id=1001,
        login="fixture-one",
        body=_issue_body(),
    )
    first = create_mentor_proposal(
        root,
        load_issue_event(first_event_path, max_body_bytes=200_000),
        _actor(1001, "fixture-one"),
        output_directory=tmp_path / "proposals",
    )
    finalize_proposal(root, first.path, moderator_github_user_id=999)

    second_event_path = _write_event(
        tmp_path,
        issue_number=11,
        user_id=2002,
        login="fixture-two",
        body=_issue_body(),
    )
    second = create_mentor_proposal(
        root,
        load_issue_event(second_event_path, max_body_bytes=200_000),
        _actor(2002, "fixture-two"),
        output_directory=tmp_path / "proposals",
    )
    assert second.proposal["match_status"] == "matched_email"
    finalize_proposal(root, second.path, moderator_github_user_id=999)

    data = load_repository(root)
    assert len(data.mentors) == 1
    assert len(data.mentors[0]["claim_ids"]) == 2
    assert len(data.mentors[0]["contacts"][0]["claim_ids"]) == 2


def test_same_email_with_different_name_is_quarantined(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    first_event_path = _write_event(
        tmp_path,
        issue_number=10,
        user_id=1001,
        login="fixture-one",
        body=_issue_body(),
    )
    first = create_mentor_proposal(
        root,
        load_issue_event(first_event_path, max_body_bytes=200_000),
        _actor(1001, "fixture-one"),
        output_directory=tmp_path / "proposals",
    )
    finalize_proposal(root, first.path, moderator_github_user_id=999)

    conflict_event_path = _write_event(
        tmp_path,
        issue_number=12,
        user_id=3003,
        login="fixture-three",
        body=_issue_body(name="另一位导师"),
    )
    conflict = create_mentor_proposal(
        root,
        load_issue_event(conflict_event_path, max_body_bytes=200_000),
        _actor(3003, "fixture-three"),
        output_directory=tmp_path / "proposals",
    )
    assert conflict.proposal["match_status"] == "conflict"
    assert "email_name_conflict" in conflict.proposal["review_reasons"]
    with pytest.raises(SubmissionError, match="姓名"):
        finalize_proposal(root, conflict.path, moderator_github_user_id=999)


def test_young_account_is_never_auto_eligible(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    event_path = _write_event(
        tmp_path,
        issue_number=13,
        user_id=4004,
        login="new-user",
        body=_issue_body(),
    )
    result = create_mentor_proposal(
        root,
        load_issue_event(event_path, max_body_bytes=200_000),
        _actor(4004, "new-user", created_year=2026),
        output_directory=tmp_path / "proposals",
    )
    assert "account_too_young" in result.proposal["review_reasons"]
