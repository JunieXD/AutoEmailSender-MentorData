from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mentor_data.batch import create_batch_proposals
from mentor_data.errors import (
    ProposalFinalizationIssue,
    ProposalSetValidationError,
    SubmissionError,
)
from mentor_data.github_events import GitHubActor, GitHubIssueEvent
from mentor_data.internal_pulls import InternalPull
from mentor_data.io_utils import load_json, load_yaml, write_json_atomic, write_yaml_atomic
from mentor_data.organization_review import (
    REVIEW_COMMENT_MARKER,
    create_organization_review_manifest,
)
from mentor_data.promotion import (
    PromotionQueue,
    PromotionReceipt,
    PromotionSummary,
    write_github_outputs,
)
from mentor_data.proposals import create_mentor_proposal
from mentor_data.report_review import REPORT_REVIEW_COMMENT_MARKER
from mentor_data.reporting import create_report_proposal
from mentor_data.repository import load_repository
from mentor_data.uploads import SAFE_COLUMNS

from .helpers import build_test_repository, claim, mentor, save_claim, save_mentor


def _pull_payload(
    *,
    number: int,
    issue_number: int,
    kind: str = "mentor",
    draft: bool = False,
) -> dict:
    prefix = {"mentor": "submission", "batch": "batch", "report": "report"}[kind]
    status_label = "status:auto-eligible" if kind == "mentor" else "status:manual-review"
    title_prefix = {"mentor": "导师投稿", "batch": "批量投稿", "report": "信息反馈"}[kind]
    return {
        "number": number,
        "state": "open",
        "merged": False,
        "draft": draft,
        "html_url": f"https://github.com/example/repository/pull/{number}",
        "title": f"[{title_prefix}] 示例导师 {issue_number}",
        "head": {
            "ref": f"{prefix}/issue-{issue_number}",
            "sha": "a" * 40,
            "repo": {"full_name": "example/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
        "labels": [{"name": status_label}],
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


def test_attention_message_keeps_complete_batch_conflict_list() -> None:
    error = ProposalSetValidationError(
        [
            ProposalFinalizationIssue(
                proposal_id="proposal_issue_30_row_36",
                batch_row=36,
                name="黄华",
                email="hhua@example.edu",
                field="research_directions",
                message="审核后的研究方向与当前值冲突",
            ),
            ProposalFinalizationIssue(
                proposal_id="proposal_issue_30_row_65",
                batch_row=65,
                name="白慧慧",
                email="hhbai@example.edu",
                field="recent_papers",
                message="审核后的近期论文与当前值冲突",
            ),
        ]
    )

    message = PromotionQueue._attention_message(error)

    assert "落库前发现 2 项导师数据冲突" in message
    assert "表格第 36 行 黄华" in message
    assert "研究方向" in message
    assert "表格第 65 行 白慧慧" in message
    assert "近期论文" in message


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


def test_main_branch_race_is_retried_and_reported(tmp_path: Path) -> None:
    class RetryQueue(PromotionQueue):
        attempts = 0

        def _list_open_pulls(self):
            return [_pull_payload(number=88, issue_number=40)]

        def _is_ready(self, pull):
            return True

        def _promote(self, pull):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("Base branch was modified. Review and try the merge again.")

        def _refresh_pull(self, pull):
            return pull

        def _remove_attention_label(self, pull_number):
            return None

    sleeps: list[float] = []
    queue = RetryQueue(
        root=tmp_path,
        repository="example/repository",
        sleeper=sleeps.append,
    )

    summary = queue.run()

    assert summary.merged == 1
    assert summary.failed == 0
    assert summary.results[0]["pr"] == 88
    assert summary.results[0]["issue"] == 40
    assert summary.results[0]["status"] == "merged"
    assert summary.results[0]["attempts"] == 3
    assert summary.results[0]["duration_seconds"] == 0.0
    assert summary.results[0]["stage_seconds"] == {
        "cleanup": 0.0,
        "readiness": 0.0,
        "retry-wait": 0.0,
        "sync": 0.0,
    }
    assert sleeps == [1.0, 2.0]


def test_exhausted_race_is_a_retryable_failure_not_a_skip(tmp_path: Path) -> None:
    class RetryQueue(PromotionQueue):
        def _list_open_pulls(self):
            return [_pull_payload(number=88, issue_number=40)]

        def _is_ready(self, pull):
            return True

        def _promote(self, pull):
            raise RuntimeError("Base branch was modified. Review and try the merge again.")

        def _refresh_pull(self, pull):
            return pull

    summary = RetryQueue(
        root=tmp_path,
        repository="example/repository",
        max_attempts=2,
        sleeper=lambda seconds: None,
    ).run()

    assert summary.failed == 1
    assert summary.retryable == 1
    assert summary.skipped == 0
    assert summary.results[0]["status"] == "retryable"
    assert summary.results[0]["attempts"] == 2


def test_promotion_outputs_include_machine_readable_per_pull_results(tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    summary = PromotionSummary(
        scanned=1,
        merged=1,
        failed=0,
        skipped=0,
        retryable=0,
        results=(
            {
                "pr": 88,
                "issue": 40,
                "status": "merged",
                "attempts": 3,
                "duration_seconds": 12.5,
                "stage_seconds": {"validate": 4.0},
            },
        ),
        duration_seconds=12.5,
    )

    write_github_outputs(output, summary)

    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert values["retryable"] == "0"
    assert values["duration_seconds"] == "12.5"
    assert values["issue_numbers"] == "40"
    assert json.loads(values["results"]) == [
        {
            "pr": 88,
            "issue": 40,
            "status": "merged",
            "attempts": 3,
            "duration_seconds": 12.5,
            "stage_seconds": {"validate": 4.0},
        }
    ]
    assert values["publish"] == "true"


def test_batch_allowlist_preserves_requested_order(tmp_path: Path) -> None:
    class OrderedQueue(PromotionQueue):
        promoted: list[int]

        def __init__(self):
            super().__init__(
                root=tmp_path,
                repository="example/repository",
                pull_numbers=(89, 88),
            )
            self.promoted = []

        def _list_open_pulls(self):
            return [
                _pull_payload(number=88, issue_number=40),
                _pull_payload(number=89, issue_number=41),
                _pull_payload(number=90, issue_number=42),
            ]

        def _is_ready(self, pull):
            return True

        def _promote(self, pull):
            self.promoted.append(pull.number)

        def _remove_attention_label(self, pull_number):
            return None

    queue = OrderedQueue()

    summary = queue.run()

    assert queue.promoted == [89, 88]
    assert summary.scanned == 2


def test_deferred_batch_comment_requires_explicit_allowlist(tmp_path: Path) -> None:
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
    comment = {
        "body": (
            f"{REVIEW_COMMENT_MARKER}\n"
            "<!-- mentor-data-batch-submit:v1 -->\n```json\n{}\n```"
        ),
        "author_association": "OWNER",
    }

    scheduled = PromotionQueue(root=tmp_path, repository="example/repository")
    selected = PromotionQueue(
        root=tmp_path,
        repository="example/repository",
        pull_numbers=(88,),
    )
    scheduled._latest_batch_review_comment_payload = (  # type: ignore[method-assign]
        lambda selected_pull: comment
    )
    selected._latest_batch_review_comment_payload = (  # type: ignore[method-assign]
        lambda selected_pull: comment
    )

    assert scheduled._is_ready(pull) is False
    assert selected._is_ready(pull) is True

    ready_pull = InternalPull(
        number=pull.number,
        url=pull.url,
        title=pull.title,
        kind=pull.kind,
        issue_number=pull.issue_number,
        branch=pull.branch,
        head_sha=pull.head_sha,
        base_sha=pull.base_sha,
        draft=False,
        status_label=pull.status_label,
    )
    assert scheduled._is_ready(ready_pull) is False


def test_missing_pr_in_explicit_batch_allowlist_is_not_silently_ignored(tmp_path: Path) -> None:
    class MissingQueue(PromotionQueue):
        def _list_open_pulls(self):
            return [_pull_payload(number=88, issue_number=40)]

        def _is_ready(self, pull):
            return False

    summary = MissingQueue(
        root=tmp_path,
        repository="example/repository",
        pull_numbers=(88, 89),
    ).run()

    assert summary.scanned == 2
    assert summary.failed == 1
    assert summary.retryable == 1
    assert summary.results[0]["pr"] == 89
    assert summary.results[0]["status"] == "retryable"


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
    def __init__(
        self,
        root: Path,
        pull_payload: dict,
        *,
        comments: list[dict] | None = None,
        timeline: list[dict] | None = None,
    ) -> None:
        self.root = root
        self.pull_payload = pull_payload
        self.comments = comments or []
        self.timeline = timeline or []
        self.merged = False

    def __call__(self, command, **kwargs):
        if command[:4] == ["gh", "api", "--paginate", "--slurp"]:
            endpoint = command[4]
            if endpoint.endswith("/pulls?state=open&per_page=100"):
                values = [] if self.merged else [self.pull_payload]
            elif endpoint.endswith(f"/issues/{self.pull_payload['number']}/comments?per_page=100"):
                values = self.comments
            elif endpoint.endswith(f"/issues/{self.pull_payload['number']}/timeline?per_page=100"):
                values = self.timeline
            else:
                raise AssertionError(f"unexpected paginated GitHub API call: {endpoint}")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([values]))
        if command[:2] == ["gh", "api"] and len(command) == 3:
            endpoint = command[2]
            if endpoint.endswith(f"/pulls/{self.pull_payload['number']}"):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(self.pull_payload),
                )
            if "/collaborators/maintainer/permission" in endpoint:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "permission": "admin",
                            "user": {"id": 999, "login": "maintainer", "type": "User"},
                        }
                    ),
                )
            raise AssertionError(f"unexpected GitHub API call: {endpoint}")
        if command[:3] == ["gh", "pr", "edit"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[:3] == ["gh", "pr", "ready"]:
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
                index for index, value in enumerate(translated) if value.startswith("+refs/pull/")
            )
            destination = translated[pull_ref_index].split(":", 1)[1]
            translated[pull_ref_index] = (
                f"+refs/heads/{self.pull_payload['head']['ref']}:{destination}"
            )
            command = translated
        return subprocess.run(command, **kwargs)


def _initialize_local_git_repository(root: Path, tmp_path: Path) -> str:
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
    return base_sha


def _stage_internal_pull_branch(root: Path, branch: str, paths: list[Path]) -> str:
    _git(root, "switch", "-c", branch)
    _git(root, "add", "--", *(str(path.relative_to(root)) for path in paths))
    _git(root, "commit", "-m", f"stage {branch}")
    head_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "push", "origin", branch)
    _git(root, "switch", "main")
    return head_sha


def _batch_event(issue_number: int) -> GitHubIssueEvent:
    return GitHubIssueEvent(
        action="opened",
        number=issue_number,
        state="open",
        is_pull_request=False,
        url=f"https://github.com/example/repository/issues/{issue_number}",
        title="[批量投稿] 示例大学计算机学院",
        body=(
            "### 社区共享包\n\n"
            "[community.csv](https://github.com/user-attachments/assets/123e4567-e89b-12d3-a456-426614174000)\n\n"
            "### 补充说明\n\n测试批次\n\n"
            "### 投稿确认\n\n- [x] 我确认文件只包含公开职业信息"
        ),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        author_id=7007,
        author_login="batch-user",
        author_type="User",
        labels=("submission:batch",),
    )


def _batch_actor() -> GitHubActor:
    return GitHubActor(
        user_id=7007,
        login="batch-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


def _prepare_batch_pull(
    root: Path,
    tmp_path: Path,
    *,
    issue_number: int,
    valid: bool,
) -> tuple[list[Path], dict]:
    package = tmp_path / f"community-{issue_number}.csv"
    row = [
        "批量测试导师",
        "batch@example.edu" if valid else "",
        "教授",
        "示例大学",
        "计院",
        "",
        "可靠系统",
        "Batch Paper",
        "https://cs.example.edu/faculty/batch",
        "https://cs.example.edu/faculty",
    ]
    with package.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SAFE_COLUMNS)
        writer.writerow(row)
    proposal_directory = root / "proposals" / f"batch-issue-{issue_number}"
    result = create_batch_proposals(
        root,
        _batch_event(issue_number),
        _batch_actor(),
        package_path=package,
        output_directory=proposal_directory,
    )
    manifest_path = root / "reviews" / "pending" / f"batch-issue-{issue_number}.json"
    manifest = create_organization_review_manifest(
        root,
        _batch_event(issue_number),
        result,
        proposal_directory=f"proposals/batch-issue-{issue_number}",
        output_path=manifest_path,
        generated_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    return [*result.paths, manifest_path], manifest


def _organization_review_decision(issue_number: int, manifest_path: Path, manifest: dict) -> dict:
    decisions = []
    for group in manifest["groups"]:
        decisions.append(
            {
                "group_id": group["id"],
                "action": "resolve",
                "reason": None,
                "levels": [
                    {
                        "level": "university",
                        "action": "existing",
                        "organization_id": "org_example_university",
                        "organization_type": None,
                        "canonical_name": None,
                        "official_url": None,
                        "approved_domains": [],
                        "save_submitted_as_alias": False,
                    },
                    {
                        "level": "school",
                        "action": "existing",
                        "organization_id": "org_example_cs",
                        "organization_type": None,
                        "canonical_name": None,
                        "official_url": None,
                        "approved_domains": [],
                        "save_submitted_as_alias": True,
                    },
                    {
                        "level": "department",
                        "action": "skip",
                        "organization_id": None,
                        "organization_type": None,
                        "canonical_name": None,
                        "official_url": None,
                        "approved_domains": [],
                        "save_submitted_as_alias": False,
                    },
                ],
                "row_overrides": [],
            }
        )
    return {
        "schema_version": 1,
        "kind": "batch_organization_review_decision",
        "pull_request_number": 88,
        "issue_number": issue_number,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "decisions": decisions,
    }


def _review_comment(decision: dict) -> dict:
    return {
        "id": 991,
        "body": (
            f"{REVIEW_COMMENT_MARKER}\n```json\n{json.dumps(decision, ensure_ascii=False)}\n```"
        ),
        "created_at": "2026-08-03T01:00:00Z",
        "author_association": "OWNER",
        "user": {"id": 999, "login": "maintainer", "type": "User"},
    }


def _report_review_comment(decision: dict) -> dict:
    return {
        "id": 992,
        "body": (
            f"{REPORT_REVIEW_COMMENT_MARKER}\n"
            f"```json\n{json.dumps(decision, ensure_ascii=False)}\n```"
        ),
        "created_at": "2026-08-03T01:00:00Z",
        "author_association": "OWNER",
        "user": {"id": 999, "login": "maintainer", "type": "User"},
    }


def test_auto_eligible_mentor_is_rebuilt_finalized_and_merged_with_real_git(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    base_sha = _initialize_local_git_repository(root, tmp_path)

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


def test_manually_reviewed_batch_is_finalized_and_merged_with_real_git(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    base_sha = _initialize_local_git_repository(root, tmp_path)
    issue_number = 40
    paths, manifest = _prepare_batch_pull(
        root,
        tmp_path,
        issue_number=issue_number,
        valid=True,
    )
    decision = _organization_review_decision(issue_number, paths[-1], manifest)
    branch = f"batch/issue-{issue_number}"
    proposal_sha = _stage_internal_pull_branch(root, branch, paths)
    pull_payload = _pull_payload(
        number=88,
        issue_number=issue_number,
        kind="batch",
        draft=True,
    )
    pull_payload["head"]["sha"] = proposal_sha
    pull_payload["base"]["sha"] = base_sha
    runner = _LocalGitHubRunner(
        root,
        pull_payload,
        comments=[_review_comment(decision)],
    )

    summary = PromotionQueue(
        root=root,
        repository="example/repository",
        runner=runner,
    ).run()

    assert summary.merged == 1
    assert summary.failed == 0
    data = load_repository(root, validate=True)
    assert len(data.mentors) == 1
    assert len(data.claims) == 1
    assert not (root / "proposals" / f"batch-issue-{issue_number}").exists()
    resolution = load_json(root / "reviews" / "resolutions" / f"batch-issue-{issue_number}.json")
    assert len(resolution["mapped_proposal_ids"]) == 1
    assert resolution["invalid_rows"] == []


def test_all_invalid_batch_records_rows_and_merges_without_proposal_files(tmp_path: Path) -> None:
    root = build_test_repository(tmp_path)
    base_sha = _initialize_local_git_repository(root, tmp_path)
    issue_number = 40
    paths, manifest = _prepare_batch_pull(
        root,
        tmp_path,
        issue_number=issue_number,
        valid=False,
    )
    assert len(paths) == 1
    assert manifest["groups"] == []
    assert len(manifest["invalid_rows"]) == 1
    decision = _organization_review_decision(issue_number, paths[-1], manifest)
    branch = f"batch/issue-{issue_number}"
    proposal_sha = _stage_internal_pull_branch(root, branch, paths)
    pull_payload = _pull_payload(
        number=88,
        issue_number=issue_number,
        kind="batch",
        draft=True,
    )
    pull_payload["head"]["sha"] = proposal_sha
    pull_payload["base"]["sha"] = base_sha
    runner = _LocalGitHubRunner(
        root,
        pull_payload,
        comments=[_review_comment(decision)],
    )

    summary = PromotionQueue(
        root=root,
        repository="example/repository",
        runner=runner,
    ).run()

    assert summary.merged == 1
    assert summary.failed == 0
    data = load_repository(root, validate=True)
    assert data.mentors == []
    assert data.claims == []
    resolution = load_json(root / "reviews" / "resolutions" / f"batch-issue-{issue_number}.json")
    assert resolution["mapped_proposal_ids"] == []
    assert resolution["invalid_rows"] == [1]


def _seed_report_mentor(root: Path) -> None:
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


def _report_event(issue_number: int, report_type: str) -> GitHubIssueEvent:
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
    return GitHubIssueEvent(
        action="opened",
        number=issue_number,
        state="open",
        is_pull_request=False,
        url=f"https://github.com/example/repository/issues/{issue_number}",
        title="[信息反馈] 示例导师",
        body="\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items()),
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
        author_id=5005,
        author_login="reporter",
        author_type="User",
        labels=("report:data",),
    )


def _report_actor() -> GitHubActor:
    return GitHubActor(
        user_id=5005,
        login="reporter",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    (("accepted", "retired"), ("rejected", "active")),
)
def test_reviewed_report_outcomes_are_finalized_and_merged_with_real_git(
    tmp_path: Path,
    decision: str,
    expected_status: str,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_report_mentor(root)
    base_sha = _initialize_local_git_repository(root, tmp_path)
    issue_number = 40
    proposal_path = create_report_proposal(
        root,
        _report_event(
            issue_number,
            "导师已经退休" if decision == "accepted" else "字段错误",
        ),
        _report_actor(),
        output_directory=root / "reports" / "pending",
    )
    proposal = load_json(proposal_path)
    proposal["decision"] = decision
    proposal["moderator_reason"] = (
        "官方来源确认退休" if decision == "accepted" else "证据仍显示当前信息"
    )
    if decision == "accepted":
        proposal["accepted"] = {
            "status": "retired",
            "status_reason": "官网标注退休",
            "status_source_url": "https://cs.example.edu/faculty/mentor",
            "status_observed_at": "2026-08-03T00:00:00Z",
        }
    write_json_atomic(proposal_path, proposal)
    branch = f"report/issue-{issue_number}"
    proposal_sha = _stage_internal_pull_branch(root, branch, [proposal_path])
    pull_payload = _pull_payload(
        number=88,
        issue_number=issue_number,
        kind="report",
    )
    pull_payload["head"]["sha"] = proposal_sha
    pull_payload["base"]["sha"] = base_sha
    runner = _LocalGitHubRunner(
        root,
        pull_payload,
        timeline=[
            {
                "event": "ready_for_review",
                "actor": {"id": 999, "login": "maintainer", "type": "User"},
            }
        ],
    )

    summary = PromotionQueue(
        root=root,
        repository="example/repository",
        runner=runner,
    ).run()

    assert summary.merged == 1
    assert summary.failed == 0
    data = load_repository(root, validate=True)
    assert data.mentors[0]["status"] == expected_status
    resolution = next(
        item for item in data.resolutions if item["report_issue"]["number"] == issue_number
    )
    assert resolution["decision"] == decision
    assert not (root / "reports" / "pending" / f"issue-{issue_number}.json").exists()


def test_draft_report_review_comment_is_applied_and_merged_with_real_git(
    tmp_path: Path,
) -> None:
    root = build_test_repository(tmp_path)
    _seed_report_mentor(root)
    base_sha = _initialize_local_git_repository(root, tmp_path)
    issue_number = 40
    proposal_path = create_report_proposal(
        root,
        _report_event(issue_number, "字段错误"),
        _report_actor(),
        output_directory=root / "reports" / "pending",
    )
    decision = {
        "schema_version": 1,
        "kind": "report_review_decision",
        "pull_request_number": 88,
        "issue_number": issue_number,
        "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        "decision": "rejected",
        "moderator_reason": "官网仍显示当前信息，反馈不成立",
        "accepted": {},
    }
    branch = f"report/issue-{issue_number}"
    proposal_sha = _stage_internal_pull_branch(root, branch, [proposal_path])
    pull_payload = _pull_payload(
        number=88,
        issue_number=issue_number,
        kind="report",
        draft=True,
    )
    pull_payload["head"]["sha"] = proposal_sha
    pull_payload["base"]["sha"] = base_sha
    runner = _LocalGitHubRunner(
        root,
        pull_payload,
        comments=[_report_review_comment(decision)],
    )

    summary = PromotionQueue(
        root=root,
        repository="example/repository",
        runner=runner,
    ).run()

    assert summary.merged == 1
    assert summary.failed == 0
    assert runner.merged is True
    data = load_repository(root, validate=True)
    resolution = next(
        item for item in data.resolutions if item["report_issue"]["number"] == issue_number
    )
    assert resolution["decision"] == "rejected"
    assert resolution["moderator"]["github_login"] == "maintainer"
