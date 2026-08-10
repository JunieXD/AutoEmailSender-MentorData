from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import SubmissionError
from .github_events import GITHUB_LOGIN_PATTERN, parse_datetime
from .io_utils import load_json, write_json_atomic
from .organization_review import TRUSTED_REVIEW_ASSOCIATIONS

REPORT_REVIEW_COMMENT_MARKER = "<!-- mentor-data-report-review:v1 -->"


@dataclass(frozen=True, slots=True)
class ReportReviewComment:
    pull_request_number: int
    comment_id: int
    reviewer_id: int
    reviewer_login: str
    author_association: str
    created_at: datetime
    decision: dict[str, Any]


def _validate_schema(root: Path, schema_name: str, value: Any, label: str) -> None:
    schema = load_json(root / "schemas" / schema_name)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise SubmissionError(f"{label}格式无效（{location}）：{errors[0].message}")


def _parse_comment_payload(body: str) -> dict[str, Any]:
    normalized_body = body.replace("\r\n", "\n")
    if not normalized_body.startswith(REPORT_REVIEW_COMMENT_MARKER):
        raise SubmissionError("评论不是信息反馈审核指令")
    remainder = normalized_body[len(REPORT_REVIEW_COMMENT_MARKER) :].strip()
    if not remainder.startswith("```json\n") or not remainder.endswith("```"):
        raise SubmissionError("信息反馈审核评论必须包含唯一的 JSON 代码块")
    payload = remainder[len("```json\n") : -len("```")].strip()
    if "```" in payload:
        raise SubmissionError("信息反馈审核评论包含多余代码块")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SubmissionError("信息反馈审核评论不是有效 JSON") from error
    if not isinstance(value, dict):
        raise SubmissionError("信息反馈审核决策必须是 JSON 对象")
    return value


def load_report_review_comment(root: Path, event_path: Path) -> ReportReviewComment:
    event = load_json(event_path)
    issue = event.get("issue")
    comment = event.get("comment")
    if not isinstance(issue, dict) or not isinstance(issue.get("pull_request"), dict):
        raise SubmissionError("信息反馈审核指令必须发布在 Pull Request 评论中")
    if not isinstance(comment, dict):
        raise SubmissionError("评论事件缺少 comment 对象")
    pull_request_number = issue.get("number")
    comment_id = comment.get("id")
    user = comment.get("user")
    association = comment.get("author_association")
    if not isinstance(pull_request_number, int) or pull_request_number <= 0:
        raise SubmissionError("Pull Request 编号无效")
    if not isinstance(comment_id, int) or comment_id <= 0:
        raise SubmissionError("评论 ID 无效")
    if not isinstance(user, dict):
        raise SubmissionError("评论缺少审核者信息")
    reviewer_id = user.get("id")
    reviewer_login = user.get("login")
    reviewer_type = user.get("type")
    if not isinstance(reviewer_id, int) or reviewer_id <= 0:
        raise SubmissionError("审核者数字 ID 无效")
    if not isinstance(reviewer_login, str) or not GITHUB_LOGIN_PATTERN.fullmatch(reviewer_login):
        raise SubmissionError("审核者 login 无效")
    if reviewer_type != "User" or association not in TRUSTED_REVIEW_ASSOCIATIONS:
        raise SubmissionError("只有仓库所有者或受信任协作者可以审核信息反馈")
    decision = _parse_comment_payload(str(comment.get("body") or ""))
    _validate_schema(root, "report-review-decision.schema.json", decision, "信息反馈审核决策")
    if decision["pull_request_number"] != pull_request_number:
        raise SubmissionError("信息反馈审核决策中的 Pull Request 编号不一致")
    accepted = decision["accepted"]
    if decision["decision"] in {"accepted", "partially_accepted"}:
        if not accepted:
            raise SubmissionError("接受或部分接受反馈时必须填写实际采用的修改")
        _validate_schema(root, "correction-patch.schema.json", accepted, "信息反馈修改")
    elif accepted:
        raise SubmissionError("未接受的信息反馈不得包含数据修改")
    return ReportReviewComment(
        pull_request_number=pull_request_number,
        comment_id=comment_id,
        reviewer_id=reviewer_id,
        reviewer_login=reviewer_login,
        author_association=association,
        created_at=parse_datetime(str(comment.get("created_at", ""))),
        decision=decision,
    )


def apply_report_review(
    root: Path,
    proposal_path: Path,
    comment: ReportReviewComment,
    *,
    expected_issue_number: int,
) -> None:
    decision = comment.decision
    if decision["issue_number"] != expected_issue_number:
        raise SubmissionError("信息反馈审核决策中的 Issue 编号不一致")
    proposal_bytes = proposal_path.read_bytes()
    proposal_digest = hashlib.sha256(proposal_bytes).hexdigest()
    if decision["proposal_sha256"] != proposal_digest:
        raise SubmissionError("信息反馈提案已变化，请重新打开审核页确认")
    proposal = load_json(proposal_path)
    if proposal.get("issue", {}).get("number") != expected_issue_number:
        raise SubmissionError("信息反馈提案与 Issue 编号不一致")
    if proposal.get("decision") != "pending" or proposal.get("accepted") != {}:
        raise SubmissionError("信息反馈提案已经被其他审核方式修改")
    proposal["decision"] = decision["decision"]
    proposal["moderator_reason"] = decision["moderator_reason"]
    proposal["accepted"] = decision["accepted"]
    write_json_atomic(proposal_path, proposal)


def load_report_review_comment_value(
    root: Path,
    *,
    pull_request_number: int,
    comment: dict[str, Any],
) -> ReportReviewComment:
    event = {"issue": {"number": pull_request_number, "pull_request": {}}, "comment": comment}
    with tempfile.TemporaryDirectory(prefix="mentor-data-report-review-") as temporary:
        event_path = Path(temporary) / "event.json"
        event_path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
        return load_report_review_comment(root, event_path)
