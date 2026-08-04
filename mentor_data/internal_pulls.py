from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import SubmissionError

SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
BRANCH_PATTERN = re.compile(
    r"^(?P<prefix>submission|batch|report)/issue-(?P<issue>[1-9][0-9]*)$"
)
PREFIX_KIND = {"submission": "mentor", "batch": "batch", "report": "report"}
STATUS_LABELS = {"status:auto-eligible", "status:manual-review"}
MAX_PROPOSAL_FILES = 5_000
MAX_PROPOSAL_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InternalPull:
    number: int
    url: str
    title: str
    kind: str
    issue_number: int
    branch: str
    head_sha: str
    base_sha: str
    draft: bool
    status_label: str


def load_internal_pull(
    value: dict[str, Any],
    *,
    expected_repository: str,
) -> InternalPull:
    number = value.get("number")
    url = value.get("html_url")
    title = value.get("title")
    head = value.get("head")
    base = value.get("base")
    if (
        not isinstance(number, int)
        or number <= 0
        or not isinstance(url, str)
        or not isinstance(title, str)
        or not isinstance(head, dict)
        or not isinstance(base, dict)
    ):
        raise SubmissionError("内部审核 Pull Request 元数据不完整")
    if value.get("state") != "open" or value.get("merged") is True:
        raise SubmissionError("内部审核 Pull Request 必须仍然开放")
    if base.get("ref") != "main" or head.get("repo", {}).get("full_name") != expected_repository:
        raise SubmissionError("内部审核 Pull Request 必须来自本仓库并以 main 为目标")
    branch = head.get("ref")
    match = BRANCH_PATTERN.fullmatch(branch) if isinstance(branch, str) else None
    if match is None:
        raise SubmissionError("Pull Request 不是稳定的内部审核分支")
    head_sha = head.get("sha")
    base_sha = base.get("sha")
    if (
        not isinstance(head_sha, str)
        or SHA_PATTERN.fullmatch(head_sha) is None
        or not isinstance(base_sha, str)
        or SHA_PATTERN.fullmatch(base_sha) is None
    ):
        raise SubmissionError("Pull Request commit SHA 无效")
    labels = {
        item.get("name")
        for item in value.get("labels", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    status_labels = labels & STATUS_LABELS
    if len(status_labels) != 1:
        raise SubmissionError("内部审核 Pull Request 必须且只能有一个处理状态标签")
    return InternalPull(
        number=number,
        url=url,
        title=title,
        kind=PREFIX_KIND[match.group("prefix")],
        issue_number=int(match.group("issue")),
        branch=branch,
        head_sha=head_sha,
        base_sha=base_sha,
        draft=value.get("draft") is True,
        status_label=next(iter(status_labels)),
    )


def _path_is_allowed(kind: str, issue_number: int, path: str) -> bool:
    if kind == "mentor":
        return path == f"proposals/issue-{issue_number}.json"
    if kind == "report":
        return path == f"reports/pending/issue-{issue_number}.json"
    proposal_prefix = f"proposals/batch-issue-{issue_number}/"
    return (
        path.startswith(proposal_prefix)
        and path.endswith(".json")
        and len(PurePosixPath(path).parts) == 3
    ) or path == f"reviews/pending/batch-issue-{issue_number}.json"


def proposal_paths_from_diff(
    repository_root: Path,
    pull: InternalPull,
    *,
    source_sha: str | None = None,
    base_sha: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[str]:
    resolved_source = source_sha or pull.head_sha
    resolved_base = base_sha or pull.base_sha
    for value in (resolved_source, resolved_base):
        if SHA_PATTERN.fullmatch(value) is None:
            raise ValueError("用于读取提案的 commit SHA 无效")
    completed = runner(
        [
            "git",
            "-C",
            str(repository_root),
            "diff",
            "--name-status",
            "--no-renames",
            f"{resolved_base}...{resolved_source}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or parts[0] not in {"A", "M"}:
            raise SubmissionError("内部审核分支包含删除、重命名或无法识别的文件变更")
        path = parts[1]
        if not _path_is_allowed(pull.kind, pull.issue_number, path):
            raise SubmissionError(f"内部审核分支包含不允许的文件：{path}")
        paths.append(path)
    if len(paths) > MAX_PROPOSAL_FILES:
        raise SubmissionError("内部审核分支包含过多文件")
    required = {
        "mentor": f"proposals/issue-{pull.issue_number}.json",
        "report": f"reports/pending/issue-{pull.issue_number}.json",
        "batch": f"reviews/pending/batch-issue-{pull.issue_number}.json",
    }[pull.kind]
    if required not in paths:
        raise SubmissionError("内部审核分支缺少所需提案或审核清单")
    if pull.kind == "batch" and not any(
        path.startswith(f"proposals/batch-issue-{pull.issue_number}/") for path in paths
    ):
        raise SubmissionError("批量审核分支没有导师提案")
    return sorted(paths)


def materialize_proposal_paths(
    repository_root: Path,
    destination_root: Path,
    *,
    source_sha: str,
    paths: list[str],
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    if SHA_PATTERN.fullmatch(source_sha) is None:
        raise ValueError("提案来源 commit SHA 无效")
    destination_root = destination_root.resolve()
    total_bytes = 0
    for relative_path in paths:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise SubmissionError("提案文件路径不安全")
        completed = runner(
            [
                "git",
                "-C",
                str(repository_root),
                "show",
                f"{source_sha}:{relative_path}",
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        payload = completed.stdout
        if not isinstance(payload, bytes):
            raise RuntimeError("git show 没有返回原始字节")
        total_bytes += len(payload)
        if total_bytes > MAX_PROPOSAL_BYTES:
            raise SubmissionError("内部审核提案总大小超过限制")
        destination = (destination_root / Path(*pure_path.parts)).resolve()
        if destination_root not in destination.parents:
            raise SubmissionError("提案文件目标路径不安全")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
