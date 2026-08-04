from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mentor_data.errors import SubmissionError
from mentor_data.github_events import load_issue_event
from mentor_data.workflow_state import inspect_issue_workflow_state, workflow_branch

from .helpers import build_test_repository

REPOSITORY = "example/repository"


def _event(tmp_path: Path, *, number: int = 40, labels: list[str] | None = None) -> Path:
    path = tmp_path / "event.json"
    path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": number,
                    "state": "open",
                    "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
                    "title": "[导师投稿] 示例导师",
                    "body": "提交内容",
                    "created_at": "2026-08-04T00:00:00Z",
                    "user": {"id": 7007, "login": "contributor", "type": "User"},
                    "labels": [
                        {"name": value} for value in (labels or ["submission:mentor"])
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _runner(pulls: list[dict]):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(pulls))

    return run


def test_new_issue_uses_one_stable_branch(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    event = load_issue_event(_event(tmp_path), max_body_bytes=200_000)

    state = inspect_issue_workflow_state(
        root=root,
        event=event,
        repository=REPOSITORY,
        issue_number=40,
        kind="mentor",
        runner=_runner([]),
    )

    assert state.outcome == "new"
    assert state.should_process is True
    assert state.branch == "submission/issue-40"
    assert workflow_branch("batch", 40) == "batch/issue-40"
    assert workflow_branch("report", 40) == "report/issue-40"


def test_retry_reuses_the_existing_pull_request(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    event = load_issue_event(_event(tmp_path), max_body_bytes=200_000)
    pull = {
        "number": 88,
        "state": "open",
        "merged_at": None,
        "html_url": f"https://github.com/{REPOSITORY}/pull/88",
        "head": {"ref": "submission/issue-40"},
    }

    state = inspect_issue_workflow_state(
        root=root,
        event=event,
        repository=REPOSITORY,
        issue_number=40,
        kind="mentor",
        runner=_runner([pull]),
    )

    assert state.outcome == "existing_pull"
    assert state.should_process is False
    assert state.pull_number == 88


def test_issue_cannot_select_multiple_workflow_types(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    event = load_issue_event(
        _event(tmp_path, labels=["submission:mentor", "submission:batch"]),
        max_body_bytes=200_000,
    )

    with pytest.raises(SubmissionError, match="只能属于一种"):
        inspect_issue_workflow_state(
            root=root,
            event=event,
            repository=REPOSITORY,
            issue_number=40,
            kind="mentor",
            runner=_runner([]),
        )


def test_closed_issue_and_pull_request_masquerade_are_rejected(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    event_path = _event(tmp_path)
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["issue"]["state"] = "closed"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    closed = load_issue_event(event_path, max_body_bytes=200_000)
    with pytest.raises(SubmissionError, match="开放的普通 Issue"):
        inspect_issue_workflow_state(
            root=root,
            event=closed,
            repository=REPOSITORY,
            issue_number=40,
            kind="mentor",
            runner=_runner([]),
        )

    payload["issue"]["state"] = "open"
    payload["issue"]["pull_request"] = {"url": "https://api.github.com/pulls/40"}
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    pull = load_issue_event(event_path, max_body_bytes=200_000)
    with pytest.raises(SubmissionError, match="开放的普通 Issue"):
        inspect_issue_workflow_state(
            root=root,
            event=pull,
            repository=REPOSITORY,
            issue_number=40,
            kind="mentor",
            runner=_runner([]),
        )
