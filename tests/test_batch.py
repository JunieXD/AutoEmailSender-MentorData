from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

from mentor_data.batch import create_batch_proposals
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


def test_batch_duplicates_become_two_claims_for_one_mentor(tmp_path) -> None:
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

    finalize_proposal_set(root, list(result.paths), moderator_github_user_id=999)
    data = load_repository(root)
    assert len(data.mentors) == 1
    assert len(data.claims) == 2
    assert len(data.mentors[0]["claim_ids"]) == 2
