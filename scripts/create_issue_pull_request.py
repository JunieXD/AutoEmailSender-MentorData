from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from mentor_data.errors import SubmissionError
from mentor_data.github_events import load_issue_event, require_issue_trigger
from mentor_data.io_utils import load_yaml

REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)
PULL_URL_PATTERN_TEMPLATE = r"^https://github\.com/{repository}/pull/[1-9][0-9]*$"
KIND_RULES = {
    "mentor": (
        "submission:mentor",
        re.compile(
            r"^(?:automatic|submission)/issue-(?P<issue>[1-9][0-9]*)-"
            r"[1-9][0-9]*(?:-[1-9][0-9]*)?$"
        ),
    ),
    "batch": (
        "submission:batch",
        re.compile(
            r"^(?:automatic-batch|batch)/issue-(?P<issue>[1-9][0-9]*)-"
            r"[1-9][0-9]*(?:-[1-9][0-9]*)?$"
        ),
    ),
    "report": (
        "report:data",
        re.compile(
            r"^report/issue-(?P<issue>[1-9][0-9]*)-"
            r"[1-9][0-9]*(?:-[1-9][0-9]*)?$"
        ),
    ),
}


def build_pull_request_command(
    *,
    repository: str,
    head: str,
    title: str,
    body: str,
    draft: bool,
) -> list[str]:
    command = [
        "gh",
        "pr",
        "create",
        "--repo",
        repository,
        "--base",
        "main",
        "--head",
        head,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        command.append("--draft")
    return command


def create_issue_pull_request(
    *,
    root: Path,
    event_path: Path,
    repository: str,
    issue_number: int,
    kind: str,
    head: str,
    body: str,
    draft: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError("GitHub 仓库名无效")
    if issue_number <= 0:
        raise ValueError("Issue 编号必须是正整数")
    if "\x00" in body or len(body) > 10_000:
        raise ValueError("Pull Request 正文无效或过长")
    try:
        required_label, branch_pattern = KIND_RULES[kind]
    except KeyError as error:
        raise ValueError(f"不支持的投稿类型：{kind}") from error
    match = branch_pattern.fullmatch(head)
    if match is None or int(match.group("issue")) != issue_number:
        raise ValueError("内部审核分支与投稿类型或 Issue 编号不一致")

    policy = load_yaml(root / "registry" / "policy.yml")
    event = load_issue_event(
        event_path,
        max_body_bytes=policy["limits"]["max_issue_body_bytes"],
    )
    require_issue_trigger(event, expected_label=required_label)
    if event.number != issue_number:
        raise SubmissionError("源 Issue 与预期编号不一致")

    command = build_pull_request_command(
        repository=repository,
        head=head,
        title=event.title,
        body=body,
        draft=draft,
    )
    completed = runner(
        command,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    pull_url = completed.stdout.strip()
    expected_url = re.compile(
        PULL_URL_PATTERN_TEMPLATE.format(repository=re.escape(repository)),
        re.IGNORECASE,
    )
    if expected_url.fullmatch(pull_url) is None:
        raise RuntimeError("GitHub CLI 没有返回预期仓库的 Pull Request URL")
    return pull_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an internal Pull Request whose title exactly matches its source Issue."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--event", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue-number", required=True, type=int)
    parser.add_argument("--kind", required=True, choices=sorted(KIND_RULES))
    parser.add_argument("--head", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--draft", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pull_url = create_issue_pull_request(
            root=Path(args.root).resolve(),
            event_path=Path(args.event),
            repository=args.repository,
            issue_number=args.issue_number,
            kind=args.kind,
            head=args.head,
            body=args.body,
            draft=args.draft,
        )
    except (
        OSError,
        RuntimeError,
        SubmissionError,
        subprocess.CalledProcessError,
        ValueError,
    ) as error:
        print(f"ERROR: {str(error).splitlines()[0]}", file=sys.stderr)
        return 2
    print(pull_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
