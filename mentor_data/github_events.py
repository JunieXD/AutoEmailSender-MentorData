from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .errors import SubmissionError
from .io_utils import load_json

HEADING_PATTERN = re.compile(r"^### (?P<label>[^\r\n]+)\r?$", re.MULTILINE)
NO_RESPONSE_VALUES = {"_No response_", "No response", "无响应"}
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


@dataclass(frozen=True, slots=True)
class GitHubActor:
    user_id: int
    login: str
    user_type: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GitHubIssueEvent:
    action: str
    number: int
    url: str
    title: str
    body: str
    created_at: datetime
    author_id: int
    author_login: str
    author_type: str
    labels: tuple[str, ...]


def parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise SubmissionError(f"时间格式无效：{value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_issue_event(path: Path, *, max_body_bytes: int) -> GitHubIssueEvent:
    event = load_json(path)
    issue = event.get("issue")
    if not isinstance(issue, dict):
        raise SubmissionError("事件不包含 issue 对象")
    body = issue.get("body") or ""
    if not isinstance(body, str):
        raise SubmissionError("Issue body 必须是字符串")
    if len(body.encode("utf-8")) > max_body_bytes:
        raise SubmissionError("Issue body 超过策略限制")
    author = issue.get("user")
    if not isinstance(author, dict):
        raise SubmissionError("Issue 缺少 GitHub 作者对象")
    author_id = author.get("id")
    login = author.get("login")
    user_type = author.get("type")
    if not isinstance(author_id, int) or author_id <= 0:
        raise SubmissionError("GitHub 作者数字 ID 无效")
    if not isinstance(login, str) or not GITHUB_LOGIN_PATTERN.fullmatch(login):
        raise SubmissionError("GitHub 作者 login 无效")
    if user_type not in {"User", "Bot"}:
        raise SubmissionError("GitHub 作者类型不受支持")
    labels = tuple(
        item["name"]
        for item in issue.get("labels", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    number = issue.get("number")
    if not isinstance(number, int) or number <= 0:
        raise SubmissionError("Issue 编号无效")
    return GitHubIssueEvent(
        action=str(event.get("action", "")),
        number=number,
        url=str(issue.get("html_url", "")),
        title=str(issue.get("title", "")),
        body=body,
        created_at=parse_datetime(str(issue.get("created_at", ""))),
        author_id=author_id,
        author_login=login,
        author_type=user_type,
        labels=labels,
    )


def parse_issue_form(
    body: str,
    expected_labels: set[str] | Mapping[str, Collection[str]],
) -> dict[str, str]:
    if isinstance(expected_labels, set):
        aliases = {label: {label} for label in expected_labels}
    else:
        aliases = {
            canonical: {canonical, *accepted_labels}
            for canonical, accepted_labels in expected_labels.items()
        }
    canonical_by_label: dict[str, str] = {}
    for canonical, accepted_labels in aliases.items():
        for label in accepted_labels:
            existing = canonical_by_label.get(label)
            if existing is not None and existing != canonical:
                raise ValueError(f"Issue Form 标签别名重复：{label}")
            canonical_by_label[label] = canonical

    matches = list(HEADING_PATTERN.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        label = match.group("label").strip()
        canonical = canonical_by_label.get(label)
        if canonical is None:
            continue
        if canonical in sections:
            raise SubmissionError(f"Issue Form 字段重复：{label}")
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = body[value_start:value_end].strip()
        if value in NO_RESPONSE_VALUES:
            value = ""
        sections[canonical] = value
    missing = sorted(aliases.keys() - sections.keys())
    if missing:
        raise SubmissionError(f"Issue Form 缺少字段：{', '.join(missing)}")
    return sections


def fetch_github_actor(login: str, *, token: str | None = None) -> GitHubActor:
    if not GITHUB_LOGIN_PATTERN.fullmatch(login):
        raise SubmissionError("GitHub login 无效")
    quoted_login = urllib.parse.quote(login, safe="")
    request = urllib.request.Request(
        f"https://api.github.com/users/{quoted_login}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AutoEmailSender-MentorData/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    resolved_token = token or os.environ.get("GITHUB_TOKEN")
    if resolved_token:
        request.add_header("Authorization", f"Bearer {resolved_token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(1_000_001)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise SubmissionError(f"无法查询 GitHub 用户信息：{error}") from error
    if len(payload) > 1_000_000:
        raise SubmissionError("GitHub 用户响应异常过大")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SubmissionError("GitHub 用户响应不是有效 JSON") from error
    user_id = value.get("id")
    returned_login = value.get("login")
    user_type = value.get("type")
    created_at = value.get("created_at")
    if not isinstance(user_id, int) or returned_login is None or user_type not in {"User", "Bot"}:
        raise SubmissionError("GitHub 用户响应缺少必要字段")
    return GitHubActor(
        user_id=user_id,
        login=str(returned_login),
        user_type=user_type,
        created_at=parse_datetime(str(created_at)),
    )


def account_age_days(actor: GitHubActor, submitted_at: datetime) -> int:
    seconds = (submitted_at.astimezone(UTC) - actor.created_at.astimezone(UTC)).total_seconds()
    if seconds < 0:
        raise SubmissionError("GitHub 账号创建时间晚于投稿时间")
    return int(seconds // 86400)
