from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mentor_data.errors import SubmissionError
from mentor_data.io_utils import load_json, write_json_atomic
from mentor_data.report_review import (
    REPORT_REVIEW_COMMENT_MARKER,
    apply_report_review,
    load_report_review_comment,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _proposal(tmp_path: Path) -> Path:
    path = tmp_path / "reports" / "pending" / "issue-24.json"
    path.parent.mkdir(parents=True)
    write_json_atomic(
        path,
        {
            "schema_version": 1,
            "kind": "correction_report",
            "issue": {"number": 24, "url": "https://github.com/example/repository/issues/24"},
            "decision": "pending",
            "accepted": {},
            "moderator_reason": None,
        },
    )
    return path


def _event(tmp_path: Path, decision: dict, *, association: str = "OWNER") -> Path:
    path = tmp_path / "event.json"
    path.write_text(
        json.dumps(
            {
                "issue": {"number": 25, "pull_request": {}},
                "comment": {
                    "id": 991,
                    "body": (
                        f"{REPORT_REVIEW_COMMENT_MARKER}\n"
                        f"```json\n{json.dumps(decision, ensure_ascii=False)}\n```"
                    ),
                    "created_at": "2026-08-10T05:00:00Z",
                    "author_association": association,
                    "user": {"id": 999, "login": "maintainer", "type": "User"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _decision(proposal_path: Path, **overrides) -> dict:
    value = {
        "schema_version": 1,
        "kind": "report_review_decision",
        "pull_request_number": 25,
        "issue_number": 24,
        "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        "decision": "rejected",
        "moderator_reason": "官网仍显示当前邮箱，反馈不成立",
        "accepted": {},
    }
    value.update(overrides)
    return value


def test_trusted_report_review_comment_applies_to_unchanged_proposal(tmp_path: Path) -> None:
    proposal_path = _proposal(tmp_path)
    comment = load_report_review_comment(
        PROJECT_ROOT,
        _event(tmp_path, _decision(proposal_path)),
    )

    apply_report_review(tmp_path, proposal_path, comment, expected_issue_number=24)

    proposal = load_json(proposal_path)
    assert proposal["decision"] == "rejected"
    assert proposal["accepted"] == {}
    assert proposal["moderator_reason"] == "官网仍显示当前邮箱，反馈不成立"


def test_report_review_rejects_stale_proposal_digest(tmp_path: Path) -> None:
    proposal_path = _proposal(tmp_path)
    decision = _decision(proposal_path)
    proposal = load_json(proposal_path)
    proposal["changed"] = True
    write_json_atomic(proposal_path, proposal)
    comment = load_report_review_comment(PROJECT_ROOT, _event(tmp_path, decision))

    with pytest.raises(SubmissionError, match="提案已变化"):
        apply_report_review(tmp_path, proposal_path, comment, expected_issue_number=24)


def test_report_review_requires_trusted_association_and_valid_patch(tmp_path: Path) -> None:
    proposal_path = _proposal(tmp_path)
    with pytest.raises(SubmissionError, match="受信任协作者"):
        load_report_review_comment(
            PROJECT_ROOT,
            _event(tmp_path, _decision(proposal_path), association="NONE"),
        )

    invalid = _decision(
        proposal_path,
        decision="accepted",
        accepted={"contacts": [{"value": "missing-fields@example.edu"}]},
    )
    with pytest.raises(SubmissionError, match="信息反馈修改"):
        load_report_review_comment(PROJECT_ROOT, _event(tmp_path, invalid))
