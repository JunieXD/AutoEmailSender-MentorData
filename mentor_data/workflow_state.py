from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import SubmissionError
from .github_events import GitHubIssueEvent, require_issue_trigger
from .repository import RepositoryData, load_repository

REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)
KIND_RULES = {
    "mentor": ("submission:mentor", "submission"),
    "batch": ("submission:batch", "batch"),
    "report": ("report:data", "report"),
}
WORKFLOW_LABELS = {rule[0] for rule in KIND_RULES.values()}


@dataclass(frozen=True, slots=True)
class IssueWorkflowState:
    outcome: str
    branch: str
    pull_number: int | None = None
    pull_url: str | None = None

    @property
    def should_process(self) -> bool:
        return self.outcome == "new"


def workflow_branch(kind: str, issue_number: int) -> str:
    try:
        _, prefix = KIND_RULES[kind]
    except KeyError as error:
        raise ValueError(f"不支持的投稿类型：{kind}") from error
    if issue_number <= 0:
        raise ValueError("Issue 编号必须是正整数")
    return f"{prefix}/issue-{issue_number}"


def _validate_issue_repository(
    event: GitHubIssueEvent,
    *,
    repository: str,
) -> None:
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError("GitHub 仓库名无效")
    parsed = urlsplit(event.url)
    expected_path = f"/{repository}/issues/{event.number}"
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "github.com"
        or parsed.path.casefold() != expected_path.casefold()
        or parsed.query
        or parsed.fragment
    ):
        raise SubmissionError("Issue URL 与预期仓库或编号不一致")


def _issue_is_finalized(data: RepositoryData, kind: str, issue_number: int) -> bool:
    if any(
        receipt.get("kind") == kind and receipt.get("issue_number") == issue_number
        for receipt in data.promotion_receipts
    ):
        return True
    if kind in {"mentor", "batch"}:
        pending = any(
            proposal.get("issue", {}).get("number") == issue_number
            for proposal in data.proposals
        )
        if pending:
            return False
        accepted = any(
            claim.get("contributor", {}).get("issue_number") == issue_number
            for claim in data.claims
        )
        if kind == "mentor":
            return accepted
        reviewed = any(
            resolution.get("issue", {}).get("number") == issue_number
            for resolution in data.organization_review_resolutions
        )
        return accepted or reviewed
    return any(
        resolution.get("report_issue", {}).get("number") == issue_number
        for resolution in data.resolutions
    )


def _load_internal_pulls(
    *,
    repository: str,
    branch: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> list[dict]:
    owner = repository.split("/", 1)[0]
    completed = runner(
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{repository}/pulls",
            "-f",
            "state=all",
            "-f",
            f"head={owner}:{branch}",
            "-f",
            "base=main",
            "-f",
            "per_page=100",
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("GitHub Pull Request 查询结果不是有效 JSON") from error
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError("GitHub Pull Request 查询结果格式无效")
    return value


def inspect_issue_workflow_state(
    *,
    root: Path,
    event: GitHubIssueEvent,
    repository: str,
    issue_number: int,
    kind: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> IssueWorkflowState:
    try:
        expected_label, _ = KIND_RULES[kind]
    except KeyError as error:
        raise ValueError(f"不支持的投稿类型：{kind}") from error
    if event.number != issue_number:
        raise SubmissionError("源 Issue 与预期编号不一致")
    require_issue_trigger(event, expected_label=expected_label)
    labels = set(event.labels) & WORKFLOW_LABELS
    if labels != {expected_label}:
        raise SubmissionError("Issue 必须且只能属于一种投稿或反馈类型")
    _validate_issue_repository(event, repository=repository)

    branch = workflow_branch(kind, issue_number)
    data = load_repository(root, validate=True)
    finalized = _issue_is_finalized(data, kind, issue_number)
    pulls = _load_internal_pulls(repository=repository, branch=branch, runner=runner)
    open_pulls = [item for item in pulls if item.get("state") == "open"]
    if len(open_pulls) > 1:
        raise RuntimeError("同一个 Issue 存在多个开放的内部审核 Pull Request")
    if open_pulls:
        pull = open_pulls[0]
        number = pull.get("number")
        url = pull.get("html_url")
        head_ref = pull.get("head", {}).get("ref")
        if (
            not isinstance(number, int)
            or number <= 0
            or not isinstance(url, str)
            or head_ref != branch
        ):
            raise RuntimeError("内部审核 Pull Request 元数据无效")
        return IssueWorkflowState(
            outcome="existing_pull",
            branch=branch,
            pull_number=number,
            pull_url=url,
        )
    if finalized:
        return IssueWorkflowState(outcome="finalized", branch=branch)
    merged = [item for item in pulls if item.get("merged_at")]
    if merged:
        raise RuntimeError("内部审核 PR 已合并，但仓库中没有对应的最终结果")
    closed = [item for item in pulls if item.get("state") == "closed"]
    if closed:
        return IssueWorkflowState(outcome="closed_pull", branch=branch)
    return IssueWorkflowState(outcome="new", branch=branch)
