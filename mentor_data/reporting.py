from __future__ import annotations

import copy
import hashlib
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import SubmissionError
from .github_events import (
    GitHubActor,
    GitHubIssueEvent,
    has_checked_confirmation,
    parse_issue_form,
    require_issue_trigger,
)
from .io_utils import load_json, write_json_atomic
from .normalization import normalize_text, normalized_web_url
from .repository import RepositoryData, load_repository
from .resolutions import apply_resolution, mentor_before_snapshot

REPORT_FORM_LABELS = {
    "社区导师 ID": {"要反馈的导师（软件自动填写）"},
    "反馈类型": {"发现了什么问题"},
    "涉及字段": {"哪里有问题"},
    "当前社区值": {"现在显示的内容（选填）"},
    "建议值或处理方式": {"正确内容应该是什么"},
    "新的官方证据页面": {"可以证明的高校官网页面"},
    "说明": {"补充说明"},
    "反馈确认": set(),
}


def _blocked_for_report(data: RepositoryData, user_id: int) -> bool:
    return any(
        item.get("github_user_id") == user_id and "report" in item.get("scopes", [])
        for item in data.blocked.get("blocked", [])
    )


def create_report_proposal(
    root: Path,
    event: GitHubIssueEvent,
    actor: GitHubActor,
    *,
    output_directory: Path,
) -> Path:
    require_issue_trigger(event, expected_label="report:data")
    data = load_repository(root, validate=True)
    if event.author_id != actor.user_id or event.author_login.casefold() != actor.login.casefold():
        raise SubmissionError("GitHub API 用户与反馈 Issue 作者不一致")
    if _blocked_for_report(data, actor.user_id):
        raise SubmissionError("该 GitHub 用户已被禁止提交反馈")
    sections = parse_issue_form(event.body, REPORT_FORM_LABELS)
    if not has_checked_confirmation(sections["反馈确认"]):
        raise SubmissionError("反馈确认未完成")
    mentor_id = normalize_text(sections["社区导师 ID"])
    mentor = next((item for item in data.mentors if item["id"] == mentor_id), None)
    if mentor is None:
        raise SubmissionError("反馈引用的社区导师 ID 不存在")
    evidence_url = normalized_web_url(sections["新的官方证据页面"])
    if evidence_url is None:
        raise SubmissionError("反馈证据必须是安全的 HTTP 或 HTTPS URL")
    relevant_org_ids = {item["organization_id"] for item in mentor.get("affiliations", [])}
    review_reasons = ["corrections_require_manual_review"]
    if not any(data.registry.url_is_approved(evidence_url, org_id) for org_id in relevant_org_ids):
        review_reasons.append("unapproved_evidence_domain")

    proposal = {
        "schema_version": 1,
        "id": f"report_proposal_issue_{event.number}",
        "kind": "correction_report",
        "issue": {"number": event.number, "url": event.url},
        "reporter": {
            "github_user_id": actor.user_id,
            "github_login_at_submission": event.author_login,
            "submitted_at": event.created_at.isoformat().replace("+00:00", "Z"),
        },
        "mentor_id": mentor_id,
        "report_type": normalize_text(sections["反馈类型"]),
        "affected_fields": normalize_text(sections["涉及字段"]),
        "before": mentor_before_snapshot(mentor),
        "proposed": {
            "value": normalize_text(sections["建议值或处理方式"]),
            "explanation": normalize_text(sections["说明"]),
        },
        "accepted": {},
        "evidence_urls": [evidence_url],
        "decision": "pending",
        "moderator_reason": None,
        "review_reasons": review_reasons,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    schema = load_json(data.root / "schemas" / "report-proposal.schema.json")
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(proposal)
    )
    if errors:
        raise SubmissionError(f"生成的反馈提案无效：{errors[0].message}")
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"issue-{event.number}.json"
    write_json_atomic(path, proposal)
    return path


def _resolution_id(proposal: dict[str, Any]) -> str:
    seed = f"{proposal['issue']['url']}:{proposal['mentor_id']}:resolution"
    return f"resolution_{hashlib.sha256(seed.encode()).hexdigest()[:20]}"


def finalize_report_proposal(
    root: Path,
    proposal_path: Path,
    *,
    moderator_github_user_id: int,
    moderator_github_login: str,
) -> tuple[Path, Path | None]:
    data = load_repository(root, validate=True)
    proposal = load_json(proposal_path)
    schema = load_json(data.root / "schemas" / "report-proposal.schema.json")
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(proposal)
    )
    if errors:
        raise SubmissionError(f"反馈审核提案无效：{errors[0].message}")
    if proposal["decision"] == "pending":
        raise SubmissionError("反馈提案仍为 pending；合并前必须选择裁决结果")
    if not proposal.get("moderator_reason"):
        raise SubmissionError("反馈提案必须填写 moderator_reason")
    if proposal["decision"] in {"accepted", "partially_accepted"} and not proposal["accepted"]:
        raise SubmissionError("接受或部分接受反馈时必须填写 accepted 结构化补丁")
    if proposal["decision"] in {"rejected", "needs_evidence", "duplicate"} and proposal["accepted"]:
        raise SubmissionError("未接受的反馈不得包含 accepted 修改")
    resolution = {
        "schema_version": 1,
        "id": _resolution_id(proposal),
        "mentor_id": proposal["mentor_id"],
        "report_issue": copy.deepcopy(proposal["issue"]),
        "reporter": {
            "github_user_id": proposal["reporter"]["github_user_id"],
            "github_login": proposal["reporter"]["github_login_at_submission"],
        },
        "decision": proposal["decision"],
        "before": copy.deepcopy(proposal["before"]),
        "proposed": copy.deepcopy(proposal["proposed"]),
        "accepted": copy.deepcopy(proposal["accepted"]),
        "evidence_urls": copy.deepcopy(proposal["evidence_urls"]),
        "moderator": {
            "github_user_id": moderator_github_user_id,
            "github_login": moderator_github_login,
        },
        "decided_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "reason": proposal["moderator_reason"],
    }
    temporary_resolution = data.root / ".work" / f"{resolution['id']}.json"
    write_json_atomic(temporary_resolution, resolution)
    try:
        return apply_resolution(data.root, temporary_resolution)
    finally:
        temporary_resolution.unlink(missing_ok=True)


def check_report_proposal(root: Path, proposal_path: Path) -> None:
    resolved_root = root.resolve()
    resolved_proposal = proposal_path.resolve()
    try:
        relative_proposal = resolved_proposal.relative_to(resolved_root)
    except ValueError:
        relative_proposal = Path("reports") / "pending" / resolved_proposal.name
    with tempfile.TemporaryDirectory(prefix="mentor-data-report-") as temporary:
        rehearsal_root = Path(temporary) / "repository"
        shutil.copytree(
            resolved_root,
            rehearsal_root,
            ignore=shutil.ignore_patterns(".git", ".venv", "dist", ".work", "__pycache__"),
        )
        rehearsal_proposal = rehearsal_root / relative_proposal
        if not rehearsal_proposal.exists():
            rehearsal_proposal.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved_proposal, rehearsal_proposal)
        finalize_report_proposal(
            rehearsal_root,
            rehearsal_proposal,
            moderator_github_user_id=1,
            moderator_github_login="maintainer",
        )
