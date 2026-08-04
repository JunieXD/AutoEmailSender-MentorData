from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mentor_data.errors import SubmissionError
from scripts.create_issue_pull_request import create_issue_pull_request

from .helpers import build_test_repository

REPOSITORY = "example/repository"
UNTRUSTED_TITLE = '[投稿] 引号"、分号; $(touch /tmp/unsafe) `id` & 仍是普通标题'


def _event_path(
    tmp_path: Path,
    *,
    number: int,
    label: str,
    title: str = UNTRUSTED_TITLE,
) -> Path:
    path = tmp_path / f"event-{number}.json"
    path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": number,
                    "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
                    "title": title,
                    "body": "提交内容",
                    "created_at": "2026-08-04T00:00:00Z",
                    "user": {"id": 7007, "login": "contributor", "type": "User"},
                    "labels": [{"name": label}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("kind", "label", "head", "draft"),
    [
        ("mentor", "submission:mentor", "submission/issue-40-123456-1", True),
        ("batch", "submission:batch", "batch/issue-40-123456-2", True),
        ("report", "report:data", "report/issue-40-123456-3", True),
        ("mentor", "submission:mentor", "automatic/issue-40-123456-4", False),
        ("batch", "submission:batch", "automatic-batch/issue-40-123456-5", False),
    ],
)
def test_every_issue_flow_uses_the_exact_untrusted_title_as_one_process_argument(
    tmp_path: Path,
    kind: str,
    label: str,
    head: str,
    draft: bool,
) -> None:
    root = build_test_repository(tmp_path)
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"https://github.com/{REPOSITORY}/pull/88\n",
        )

    pull_url = create_issue_pull_request(
        root=root,
        event_path=_event_path(tmp_path, number=40, label=label),
        repository=REPOSITORY,
        issue_number=40,
        kind=kind,
        head=head,
        body="固定审核说明",
        draft=draft,
        runner=runner,
    )

    assert pull_url == f"https://github.com/{REPOSITORY}/pull/88"
    assert len(calls) == 1
    command, options = calls[0]
    assert command[command.index("--title") + 1] == UNTRUSTED_TITLE
    assert command.count(UNTRUSTED_TITLE) == 1
    assert ("--draft" in command) is draft
    assert options == {"check": True, "stdout": subprocess.PIPE, "text": True}
    assert "shell" not in options


def test_pull_creation_rejects_a_mismatched_issue_label_before_invoking_github(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("GitHub CLI must not run")

    with pytest.raises(SubmissionError, match="submission:batch"):
        create_issue_pull_request(
            root=root,
            event_path=_event_path(tmp_path, number=40, label="report:data"),
            repository=REPOSITORY,
            issue_number=40,
            kind="batch",
            head="batch/issue-40-123456-1",
            body="固定审核说明",
            draft=True,
            runner=runner,
        )

    assert called is False


def test_pull_creation_rejects_a_branch_for_another_issue(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)

    with pytest.raises(ValueError, match="Issue 编号不一致"):
        create_issue_pull_request(
            root=root,
            event_path=_event_path(tmp_path, number=40, label="submission:mentor"),
            repository=REPOSITORY,
            issue_number=40,
            kind="mentor",
            head="submission/issue-41-123456-1",
            body="固定审核说明",
            draft=True,
        )


def test_pull_creation_rejects_titles_with_line_breaks(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)

    with pytest.raises(SubmissionError, match="控制字符"):
        create_issue_pull_request(
            root=root,
            event_path=_event_path(
                tmp_path,
                number=40,
                label="submission:mentor",
                title="第一行\n第二行",
            ),
            repository=REPOSITORY,
            issue_number=40,
            kind="mentor",
            head="submission/issue-40-123456-1",
            body="固定审核说明",
            draft=True,
        )
