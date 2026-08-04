from __future__ import annotations

import json
from datetime import UTC, datetime

from mentor_data.github_events import GitHubActor, load_issue_event
from mentor_data.io_utils import load_json, write_json_atomic
from mentor_data.reporting import (
    check_report_proposal,
    create_report_proposal,
    finalize_report_proposal,
)
from mentor_data.repository import load_repository

from .helpers import build_test_repository, claim, mentor, save_claim, save_mentor


def _seed(root) -> None:
    value = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=1,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, value)
    save_mentor(root, mentor())


def _body(report_type: str) -> str:
    sections = {
        "社区导师 ID": "mentor_fixture_0001",
        "反馈类型": report_type,
        "涉及字段": "导师状态",
        "当前社区值": "active",
        "建议值或处理方式": "标记为退休",
        "新的官方证据页面": "https://cs.example.edu/faculty/mentor",
        "说明": "官方页面已经标注退休。",
        "反馈确认": "- [x] 我确认反馈基于真实官方证据",
    }
    return "\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items())


def _event(tmp_path, issue_number: int, report_type: str):
    path = tmp_path / f"report-event-{issue_number}.json"
    path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": issue_number,
                    "state": "open",
                    "html_url": f"https://github.com/example/repository/issues/{issue_number}",
                    "title": "[信息反馈] 示例导师",
                    "body": _body(report_type),
                    "created_at": "2026-08-03T00:00:00Z",
                    "user": {"id": 5005, "login": "reporter", "type": "User"},
                    "labels": [{"name": "report:data"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return load_issue_event(path, max_body_bytes=200_000)


def _actor() -> GitHubActor:
    return GitHubActor(
        user_id=5005,
        login="reporter",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


def test_true_retirement_report_is_editable_and_applied_only_after_review(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    _seed(root)
    path = create_report_proposal(
        root,
        _event(tmp_path, 20, "导师已经退休"),
        _actor(),
        output_directory=root / "reports" / "pending",
    )
    pending = load_json(path)
    assert pending["decision"] == "pending"
    assert load_repository(root).mentors[0]["status"] == "active"

    pending["decision"] = "accepted"
    pending["accepted"] = {
        "status": "retired",
        "status_reason": "官网标注退休",
        "status_source_url": "https://cs.example.edu/faculty/mentor",
        "status_observed_at": "2026-08-03T00:00:00Z",
    }
    pending["moderator_reason"] = "官方来源确认退休"
    write_json_atomic(path, pending)
    check_report_proposal(root, path)
    finalize_report_proposal(
        root,
        path,
        moderator_github_user_id=999,
        moderator_github_login="maintainer",
    )
    assert load_repository(root).mentors[0]["status"] == "retired"


def test_false_report_can_be_rejected_without_changing_mentor(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    _seed(root)
    path = create_report_proposal(
        root,
        _event(tmp_path, 21, "字段错误"),
        _actor(),
        output_directory=root / "reports" / "pending",
    )
    pending = load_json(path)
    pending["decision"] = "rejected"
    pending["moderator_reason"] = "证据页面仍显示当前信息，反馈不成立"
    write_json_atomic(path, pending)
    check_report_proposal(root, path)
    resolution_path, changed_mentor = finalize_report_proposal(
        root,
        path,
        moderator_github_user_id=999,
        moderator_github_login="maintainer",
    )
    assert changed_mentor is None
    assert resolution_path.exists()
    assert load_repository(root).mentors[0]["status"] == "active"
