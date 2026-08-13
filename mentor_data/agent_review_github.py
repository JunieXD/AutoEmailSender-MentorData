from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .agent_review import COMMENT_CHARACTER_LIMIT, AgentReviewError, PullSnapshot
from .errors import SubmissionError
from .internal_pulls import BRANCH_PATTERN, InternalPull, load_internal_pull
from .organization_review import (
    BATCH_SUBMIT_MARKER,
    REVIEW_COMMENT_MARKER,
    _organization_options,
    _validate_schema,
)
from .organizations import OrganizationRegistry

REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)
MAX_MANIFEST_BYTES = 20_000_000
TRANSIENT_GITHUB_MESSAGES = (
    "eof",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "temporary failure",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "rate limit",
    "502",
    "503",
    "504",
)


class GitHubReviewClient:
    def __init__(
        self,
        *,
        repository: str,
        root: Path,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise AgentReviewError("review_repository_invalid", "GitHub 仓库名无效")
        self.repository = repository
        self.root = root.resolve()
        if max_attempts < 1 or max_attempts > 5:
            raise AgentReviewError("review_retry_invalid", "GitHub 重试次数必须在 1 到 5 之间")
        self.max_attempts = max_attempts
        self.sleeper = sleeper
        self.runner = runner

    @staticmethod
    def _is_transient(message: str) -> bool:
        normalized = message.casefold()
        return any(item in normalized for item in TRANSIENT_GITHUB_MESSAGES)

    def _run(
        self,
        command: list[str],
        *,
        text: bool = True,
        input_value: str | None = None,
        cwd: Path | None = None,
        retry_transient: bool = False,
    ) -> subprocess.CompletedProcess:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.runner(
                    command,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=text,
                    input=input_value,
                    cwd=cwd,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                stderr = getattr(error, "stderr", None)
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                message = str(stderr or error).splitlines()[0][:500]
                if (
                    retry_transient
                    and attempt < self.max_attempts
                    and self._is_transient(message)
                ):
                    self.sleeper(float(2 ** (attempt - 1)))
                    continue
                raise AgentReviewError(
                    "review_github_unavailable",
                    f"GitHub 命令失败：{message}",
                ) from error
        raise AssertionError("unreachable")

    def _json(self, endpoint: str) -> Any:
        completed = self._run(["gh", "api", endpoint], retry_transient=True)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise AgentReviewError(
                "review_github_response_invalid",
                "GitHub API 没有返回有效 JSON",
            ) from error

    def _paginated(self, endpoint: str, *, max_items: int = 10_000) -> list[Any]:
        completed = self._run(
            ["gh", "api", "--paginate", "--slurp", endpoint],
            retry_transient=True,
        )
        try:
            pages = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise AgentReviewError(
                "review_github_response_invalid",
                "GitHub API 分页结果不是有效 JSON",
            ) from error
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise AgentReviewError(
                "review_github_response_invalid",
                "GitHub API 分页结果格式无效",
            )
        values = [item for page in pages for item in page]
        if len(values) > max_items:
            raise AgentReviewError(
                "review_github_response_too_large",
                f"GitHub API 返回超过 {max_items} 条记录",
            )
        return values

    @staticmethod
    def _snapshot(pull: InternalPull) -> PullSnapshot:
        return PullSnapshot(
            number=pull.number,
            issue_number=pull.issue_number,
            title=pull.title,
            url=pull.url,
            branch=pull.branch,
            head_sha=pull.head_sha,
            base_sha=pull.base_sha,
            draft=pull.draft,
            status_label=pull.status_label,
        )

    def get_open_batch_pull(self, pull_number: int) -> PullSnapshot:
        value = self._json(f"repos/{self.repository}/pulls/{pull_number}")
        if not isinstance(value, dict):
            raise AgentReviewError("review_pull_invalid", "Pull Request 元数据格式无效")
        try:
            pull = load_internal_pull(value, expected_repository=self.repository)
        except SubmissionError as error:
            raise AgentReviewError("review_pull_invalid", str(error)) from error
        if pull.kind != "batch":
            raise AgentReviewError(
                "review_pull_not_batch",
                f"PR #{pull_number} 不是批量导师投稿",
            )
        return self._snapshot(pull)

    def list_open_batch_pulls(self) -> list[PullSnapshot]:
        values = self._paginated(
            f"repos/{self.repository}/pulls?state=open&per_page=100",
            max_items=999,
        )
        pulls: list[PullSnapshot] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                pull = load_internal_pull(value, expected_repository=self.repository)
            except SubmissionError:
                continue
            if pull.kind == "batch":
                pulls.append(self._snapshot(pull))
        return sorted(pulls, key=lambda item: item.number)

    def fetch_manifest(self, pull: PullSnapshot) -> tuple[dict[str, Any], bytes]:
        path = f"reviews/pending/batch-issue-{pull.issue_number}.json"
        endpoint = (
            f"repos/{self.repository}/contents/{path}?ref="
            f"{quote(pull.head_sha, safe='')}"
        )
        completed = self._run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw+json",
                endpoint,
            ],
            text=False,
            retry_transient=True,
        )
        payload = completed.stdout
        if not isinstance(payload, bytes):
            payload = str(payload).encode("utf-8")
        if not payload or len(payload) > MAX_MANIFEST_BYTES:
            raise AgentReviewError(
                "review_manifest_size_invalid",
                "审核清单为空或超过 20 MB 安全上限",
            )
        try:
            manifest = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentReviewError(
                "review_manifest_invalid",
                "审核清单不是有效的 UTF-8 JSON",
            ) from error
        if not isinstance(manifest, dict):
            raise AgentReviewError("review_manifest_invalid", "审核清单必须是 JSON 对象")
        try:
            _validate_schema(
                self.root,
                "organization-review.schema.json",
                manifest,
                "机构审核清单",
            )
        except (OSError, SubmissionError, ValueError) as error:
            raise AgentReviewError("review_manifest_invalid", str(error)) from error
        if manifest.get("issue", {}).get("number") != pull.issue_number:
            raise AgentReviewError("review_manifest_invalid", "审核清单的 Issue 编号不一致")
        return manifest, payload

    def fetch_review_bundle(self, pull_number: int) -> tuple[PullSnapshot, dict[str, Any], bytes]:
        pull = self.get_open_batch_pull(pull_number)
        manifest, payload = self.fetch_manifest(pull)
        return pull, manifest, payload

    def fetch_main_organizations(self) -> list[dict[str, Any]]:
        completed = self._run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw+json",
                f"repos/{self.repository}/contents/registry/organizations.yml?ref=main",
            ],
            retry_transient=True,
        )
        try:
            document = yaml.safe_load(completed.stdout)
        except yaml.YAMLError as error:
            raise AgentReviewError(
                "review_github_response_invalid",
                "最新 main 的机构注册表不是有效 YAML",
            ) from error
        if not isinstance(document, dict):
            raise AgentReviewError("review_github_response_invalid", "最新机构注册表格式无效")
        _validate_schema(self.root, "organization.schema.json", document, "最新机构注册表")
        return _organization_options(OrganizationRegistry(document["organizations"]))

    def fetch_for_preflight(self, pull: PullSnapshot) -> None:
        self._run(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
                f"+refs/pull/{pull.number}/head:refs/remotes/origin/agent-review-{pull.number}",
            ],
            cwd=self.root,
            retry_transient=True,
        )
        fetched = self._run(
            ["git", "rev-parse", f"refs/remotes/origin/agent-review-{pull.number}"],
            cwd=self.root,
        ).stdout.strip()
        if fetched != pull.head_sha:
            raise AgentReviewError(
                "review_pull_changed",
                f"PR #{pull.number} 在预演前发生了变化",
                next_command=f"mentor-data review plan --pr {pull.number} --reset",
            )

    def comments(self, pull_number: int) -> list[dict[str, Any]]:
        return [
            item
            for item in self._paginated(
                f"repos/{self.repository}/issues/{pull_number}/comments?per_page=100"
            )
            if isinstance(item, dict)
        ]

    def official_review_comments(self, pull_number: int) -> list[dict[str, Any]]:
        return [
            item
            for item in self.comments(pull_number)
            if str(item.get("body") or "").startswith(REVIEW_COMMENT_MARKER)
        ]

    def submit_review_comment(
        self,
        pull_number: int,
        body: str,
        *,
        suppress_trigger: bool = False,
    ) -> dict[str, Any]:
        body = self.review_comment_body(body, suppress_trigger=suppress_trigger)
        completed = self._run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{self.repository}/issues/{pull_number}/comments",
                "--input",
                "-",
            ],
            input_value=json.dumps({"body": body}, ensure_ascii=False),
        )
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise AgentReviewError(
                "review_github_response_invalid",
                "GitHub 没有返回有效的审核评论",
            ) from error
        if not isinstance(value, dict) or not isinstance(value.get("id"), int):
            raise AgentReviewError(
                "review_github_response_invalid",
                "GitHub 返回的审核评论缺少 ID",
            )
        return value

    @staticmethod
    def review_comment_body(body: str, *, suppress_trigger: bool) -> str:
        if suppress_trigger:
            marker, separator, remainder = body.partition("\n")
            if marker != REVIEW_COMMENT_MARKER or not separator:
                raise AgentReviewError(
                    "review_comment_invalid",
                    "批量审核评论缺少正式审核标记",
                )
            body = f"{marker}\n{BATCH_SUBMIT_MARKER}\n{remainder}"
        if len(body) > COMMENT_CHARACTER_LIMIT:
            raise AgentReviewError(
                "review_comment_too_large",
                f"审核评论有 {len(body)} 个字符，超过 {COMMENT_CHARACTER_LIMIT} 字符上限",
            )
        return body

    def dispatch_promotion_queue(self, pull_numbers: list[int]) -> dict[str, Any]:
        if not pull_numbers or any(item <= 0 for item in pull_numbers):
            raise AgentReviewError("review_prs_invalid", "可信队列需要有效的 PR 编号")
        completed = self._run(
            [
                "gh",
                "workflow",
                "run",
                "promote-ready-pulls.yml",
                "--repo",
                self.repository,
                "--ref",
                "main",
                "-f",
                "pull_numbers=" + ",".join(str(item) for item in pull_numbers),
            ]
        )
        return {
            "workflow": "promote-ready-pulls.yml",
            "dispatched": True,
            "output": str(completed.stdout or "").strip() or None,
        }

    def retry_promotion(self, pull_number: int) -> dict[str, Any]:
        value = self._json(f"repos/{self.repository}/pulls/{pull_number}")
        if not isinstance(value, dict):
            raise AgentReviewError("review_pull_invalid", "Pull Request 元数据格式无效")
        try:
            pull = load_internal_pull(value, expected_repository=self.repository)
        except SubmissionError as error:
            raise AgentReviewError(
                "review_retry_not_open",
                f"PR #{pull_number} 已不是待落库的内部 PR：{error}",
            ) from error
        if pull.kind != "batch":
            raise AgentReviewError(
                "review_pull_not_batch",
                f"PR #{pull_number} 不是批量导师投稿",
            )
        if not self.official_review_comments(pull_number):
            raise AgentReviewError(
                "review_retry_not_approved",
                f"PR #{pull_number} 尚无正式审核评论，不能触发重试",
            )
        completed = self._run(
            [
                "gh",
                "workflow",
                "run",
                "promote-ready-pulls.yml",
                "--repo",
                self.repository,
                "--ref",
                "main",
                "-f",
                f"pull_number={pull_number}",
            ]
        )
        return {
            "pr": pull_number,
            "workflow": "promote-ready-pulls.yml",
            "dispatched": True,
            "output": str(completed.stdout or "").strip() or None,
        }

    def status(self, pull_number: int, *, issue_number: int | None = None) -> dict[str, Any]:
        pull = self._json(f"repos/{self.repository}/pulls/{pull_number}")
        if not isinstance(pull, dict):
            raise AgentReviewError("review_pull_invalid", "Pull Request 元数据格式无效")
        branch = pull.get("head", {}).get("ref")
        match = BRANCH_PATTERN.fullmatch(branch) if isinstance(branch, str) else None
        resolved_issue = issue_number or (
            int(match.group("issue")) if match and match.group("prefix") == "batch" else None
        )
        issue = (
            self._json(f"repos/{self.repository}/issues/{resolved_issue}")
            if resolved_issue is not None
            else None
        )
        comments = self.official_review_comments(pull_number)
        promotion_run = None
        if comments:
            try:
                runs_payload = self._json(
                    f"repos/{self.repository}/actions/workflows/"
                    "promote-ready-pulls.yml/runs?per_page=50"
                )
            except AgentReviewError:
                runs_payload = None
            runs = (
                runs_payload.get("workflow_runs", [])
                if isinstance(runs_payload, dict)
                else []
            )
            matching_runs = []
            for run in runs:
                if not isinstance(run, dict):
                    continue
                title = run.get("display_title")
                title_matches = isinstance(title, str) and re.search(
                    rf"(?:\bPR\s*#{pull_number}\b|\bPRs\s+[0-9,]*\b{pull_number}\b)",
                    title,
                    flags=re.IGNORECASE,
                )
                pull_requests = run.get("pull_requests", [])
                pull_matches = isinstance(pull_requests, list) and any(
                    isinstance(item, dict) and item.get("number") == pull_number
                    for item in pull_requests
                )
                if title_matches or pull_matches:
                    matching_runs.append(run)
            if matching_runs:
                selected_run = max(
                    matching_runs,
                    key=lambda item: (item.get("created_at") or "", item.get("id") or 0),
                )
                promotion_run = {
                    "id": selected_run.get("id"),
                    "event": selected_run.get("event"),
                    "status": selected_run.get("status"),
                    "conclusion": selected_run.get("conclusion"),
                    "created_at": selected_run.get("created_at"),
                    "updated_at": selected_run.get("updated_at"),
                    "url": selected_run.get("html_url"),
                }
        head_sha = pull.get("head", {}).get("sha")
        check_runs: list[dict[str, Any]] = []
        if isinstance(head_sha, str):
            checks = self._json(f"repos/{self.repository}/commits/{head_sha}/check-runs")
            if isinstance(checks, dict) and isinstance(checks.get("check_runs"), list):
                check_runs = [item for item in checks["check_runs"] if isinstance(item, dict)]
        labels = sorted(
            item["name"]
            for item in pull.get("labels", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        return {
            "pr": pull_number,
            "pr_state": pull.get("state"),
            "draft": pull.get("draft") is True,
            "merged": pull.get("merged") is True,
            "merged_at": pull.get("merged_at"),
            "updated_at": pull.get("updated_at"),
            "labels": labels,
            "issue": resolved_issue,
            "issue_state": issue.get("state") if isinstance(issue, dict) else None,
            "review_comments": len(comments),
            "latest_review_comment_id": comments[-1].get("id") if comments else None,
            "promotion_run": promotion_run,
            "checks": {
                "total": len(check_runs),
                "pending": sum(item.get("status") != "completed" for item in check_runs),
                "failed": sum(
                    item.get("status") == "completed"
                    and item.get("conclusion") not in {"success", "skipped", "neutral"}
                    for item in check_runs
                ),
            },
        }
