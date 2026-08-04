from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from mentor_data.errors import SubmissionError
from mentor_data.github_events import GitHubActor, GitHubIssueEvent
from mentor_data.internal_pulls import InternalPull
from mentor_data.io_utils import load_yaml, write_yaml_atomic
from mentor_data.promotion import PromotionQueue, PromotionReceipt
from mentor_data.proposals import create_mentor_proposal
from mentor_data.repository import load_repository

from .helpers import build_test_repository


def _pull_payload(*, number: int, issue_number: int) -> dict:
    return {
        "number": number,
        "state": "open",
        "merged": False,
        "draft": False,
        "html_url": f"https://github.com/example/repository/pull/{number}",
        "title": f"[导师投稿] 示例导师 {issue_number}",
        "head": {
            "ref": f"submission/issue-{issue_number}",
            "sha": "a" * 40,
            "repo": {"full_name": "example/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
        "labels": [{"name": "status:auto-eligible"}],
    }


def _internal_pull(*, number: int = 88, issue_number: int = 40) -> InternalPull:
    return InternalPull(
        number=number,
        url=f"https://github.com/example/repository/pull/{number}",
        title=f"[导师投稿] 示例导师 {issue_number}",
        kind="mentor",
        issue_number=issue_number,
        branch=f"submission/issue-{issue_number}",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=False,
        status_label="status:auto-eligible",
    )


def test_paginated_github_lists_are_flattened_without_shell_interpolation(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([[{"id": 1}], [{"id": 2}]]),
        )

    queue = PromotionQueue(
        root=tmp_path,
        repository="example/repository",
        runner=runner,
    )

    assert queue._gh_paginated_list("repos/example/repository/issues/88/comments?per_page=100") == [
        {"id": 1},
        {"id": 2},
    ]
    assert calls[0][0] == [
        "gh",
        "api",
        "--paginate",
        "--slurp",
        "repos/example/repository/issues/88/comments?per_page=100",
    ]
    assert "shell" not in calls[0][1]


def test_open_pull_queue_is_loaded_in_one_paginated_api_call(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    [_pull_payload(number=89, issue_number=41)],
                    [_pull_payload(number=88, issue_number=40)],
                ]
            ),
        )

    queue = PromotionQueue(
        root=tmp_path,
        repository="example/repository",
        runner=runner,
    )

    assert [item["number"] for item in queue._list_open_pulls()] == [88, 89]
    assert calls == [
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/example/repository/pulls?state=open&per_page=100",
        ]
    ]


def test_untrusted_marker_comment_cannot_override_a_batch_review(tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    [
                        {
                            "id": 1,
                            "body": "<!-- mentor-data-organization-review:v1 -->\n```json\n{}\n```",
                            "author_association": "NONE",
                            "user": {"id": 123, "login": "outsider", "type": "User"},
                        }
                    ]
                ]
            ),
        )

    queue = PromotionQueue(
        root=tmp_path,
        repository="example/repository",
        runner=runner,
    )
    pull = InternalPull(
        number=88,
        url="https://github.com/example/repository/pull/88",
        title="[批量投稿] 示例大学",
        kind="batch",
        issue_number=40,
        branch="batch/issue-40",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )

    assert queue._latest_batch_review_comment(pull, required=False) is None


def test_one_invalid_pull_and_attention_failure_do_not_block_the_next_pull(
    tmp_path: Path,
) -> None:
    class IsolatedFailureQueue(PromotionQueue):
        promoted: list[int]

        def __init__(self) -> None:
            super().__init__(root=tmp_path, repository="example/repository")
            self.promoted = []

        def _list_open_pulls(self):
            return [
                _pull_payload(number=88, issue_number=40),
                _pull_payload(number=89, issue_number=41),
            ]

        def _is_ready(self, pull):
            return True

        def _promote(self, pull):
            if pull.number == 88:
                raise SubmissionError("第一条提案无效")
            self.promoted.append(pull.number)

        def _mark_attention(self, pull, message):
            raise RuntimeError("GitHub 暂时无法写入标签")

        def _remove_attention_label(self, pull_number):
            return None

    queue = IsolatedFailureQueue()

    summary = queue.run()

    assert summary.scanned == 2
    assert summary.failed == 1
    assert summary.merged == 1
    assert queue.promoted == [89]


def test_finalized_branch_receipt_recovers_without_reapplying_the_proposal(
    tmp_path: Path,
) -> None:
    class RecoveryQueue(PromotionQueue):
        validated = False
        merged = False

        def _fetch_commits(self, pull):
            return None

        def _origin_main_sha(self):
            return "b" * 40

        def _load_receipt(self, pull):
            return PromotionReceipt(
                kind=pull.kind,
                issue_number=pull.issue_number,
                pull_number=pull.number,
                base_sha="b" * 40,
                proposal_commit_sha="c" * 40,
            )

        def _validate_final_branch(self, pull, *, base_sha):
            assert base_sha == "b" * 40
            self.validated = True

        def _merge_pull(self, pull, *, expected_head_sha, expected_base_sha):
            assert expected_head_sha == pull.head_sha
            assert expected_base_sha == "b" * 40
            self.merged = True

    queue = RecoveryQueue(root=tmp_path, repository="example/repository")

    queue._promote(_internal_pull())

    assert queue.validated is True
    assert queue.merged is True


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _automatic_proposal(root: Path, output: Path, *, issue_number: int) -> Path:
    sections = {
        "导师姓名": "队列测试导师",
        "公开工作邮箱": "queue@example.edu",
        "学校正式名称": "示例大学",
        "学院或研究院正式名称": "计算机学院",
        "系所或中心": "_No response_",
        "职称": "教授",
        "研究方向": "可靠系统",
        "近期或代表论文": "Queue Paper",
        "高校官网导师详情页": "https://cs.example.edu/faculty/queue",
        "发现导师的来源页": "https://cs.example.edu/faculty",
        "投稿确认": "- [x] 我确认提交的是公开职业信息",
    }
    event = GitHubIssueEvent(
        action="opened",
        number=issue_number,
        state="open",
        is_pull_request=False,
        url=f"https://github.com/example/repository/issues/{issue_number}",
        title="[导师投稿] 示例大学队列测试导师",
        body="\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items()),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        author_id=8040,
        author_login="queue-user",
        author_type="User",
        labels=("submission:mentor",),
    )
    actor = GitHubActor(
        user_id=8040,
        login="queue-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    result = create_mentor_proposal(root, event, actor, output_directory=output)
    assert result.proposal["auto_eligible"] is True
    return result.path


class _LocalGitHubRunner:
    def __init__(self, root: Path, pull_payload: dict) -> None:
        self.root = root
        self.pull_payload = pull_payload
        self.merged = False

    def __call__(self, command, **kwargs):
        if command[:4] == ["gh", "api", "--paginate", "--slurp"] and command[
            4
        ].endswith("/pulls?state=open&per_page=100"):
            values = [] if self.merged else [self.pull_payload]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([values]))
        if command[:2] == ["gh", "api"] and len(command) == 3:
            endpoint = command[2]
            if endpoint.endswith(f"/pulls/{self.pull_payload['number']}"):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(self.pull_payload),
                )
        if command[:3] == ["gh", "pr", "edit"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[:3] == ["gh", "pr", "merge"]:
            number = self.pull_payload["number"]
            branch = self.pull_payload["head"]["ref"]
            destination = f"refs/remotes/origin/test-merge-{number}"
            _git(
                self.root,
                "fetch",
                "--no-tags",
                "origin",
                f"+refs/heads/{branch}:{destination}",
            )
            expected_sha = command[command.index("--match-head-commit") + 1]
            assert _git(self.root, "rev-parse", destination) == expected_sha
            _git(self.root, "merge", "--squash", destination)
            _git(self.root, "commit", "-m", f"merge pull request #{number}")
            _git(self.root, "push", "origin", "main")
            self.merged = True
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[:3] == ["git", "fetch", "--no-tags"] and any(
            value.startswith("+refs/pull/") for value in command
        ):
            translated = list(command)
            pull_ref_index = next(
                index
                for index, value in enumerate(translated)
                if value.startswith("+refs/pull/")
            )
            destination = translated[pull_ref_index].split(":", 1)[1]
            translated[pull_ref_index] = (
                f"+refs/heads/{self.pull_payload['head']['ref']}:{destination}"
            )
            command = translated
        return subprocess.run(command, **kwargs)


def test_auto_eligible_mentor_is_rebuilt_finalized_and_merged_with_real_git(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    policy_path = root / "registry" / "policy.yml"
    policy = load_yaml(policy_path)
    policy["automation"]["auto_merge_enabled"] = True
    write_yaml_atomic(policy_path, policy)
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "Test Maintainer")
    _git(root, "config", "user.email", "maintainer@example.test")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial canonical data")
    base_sha = _git(root, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "--set-upstream", "origin", "main")

    issue_number = 40
    proposal = _automatic_proposal(root, tmp_path / "prepared", issue_number=issue_number)
    branch = f"submission/issue-{issue_number}"
    _git(root, "switch", "-c", branch)
    destination = root / "proposals" / f"issue-{issue_number}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(proposal, destination)
    _git(root, "add", str(destination.relative_to(root)))
    _git(root, "commit", "-m", f"stage proposal #{issue_number}")
    proposal_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "push", "origin", branch)
    _git(root, "switch", "main")

    pull_payload = _pull_payload(number=88, issue_number=issue_number)
    pull_payload["head"]["sha"] = proposal_sha
    pull_payload["base"]["sha"] = base_sha
    runner = _LocalGitHubRunner(root, pull_payload)
    queue = PromotionQueue(
        root=root,
        repository="example/repository",
        runner=runner,
    )

    summary = queue.run()

    assert summary.merged == 1
    assert summary.failed == 0
    assert runner.merged is True
    data = load_repository(root, validate=True)
    assert len(data.mentors) == 1
    assert len(data.claims) == 1
    assert data.proposals == []
    assert data.promotion_receipts[0]["issue_number"] == issue_number
