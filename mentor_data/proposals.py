from __future__ import annotations

import copy
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import SubmissionError
from .github_events import GitHubActor, GitHubIssueEvent, account_age_days, parse_issue_form
from .identifiers import proposed_mentor_id, stable_proposal_entity_id
from .io_utils import load_json, write_json_atomic
from .normalization import (
    is_generic_email,
    is_valid_email,
    normalize_email,
    normalize_name_key,
    normalize_text,
    normalized_web_url,
)
from .repository import RepositoryData, load_repository

SINGLE_FORM_LABELS = {
    "导师姓名",
    "公开工作邮箱",
    "社区机构 ID",
    "学校正式名称",
    "学院或研究院正式名称",
    "系所或中心",
    "职称",
    "研究方向",
    "近期或代表论文",
    "官方个人主页",
    "官方证据页面",
    "投稿确认",
}
SUPPORTED_TITLES = {
    "教授",
    "副教授",
    "助理教授",
    "讲师",
    "研究员",
    "副研究员",
    "助理研究员",
    "特聘研究员",
}
SPLIT_DIRECTIONS = re.compile(r"[；;\r\n]+")
SPLIT_PAPERS = re.compile(r"[|；;\r\n]+")


@dataclass(frozen=True, slots=True)
class ProposalResult:
    proposal: dict[str, Any]
    path: Path


@dataclass(slots=True)
class _FinalizationContext:
    data: RepositoryData
    proposal_schema: dict[str, Any]
    claim_by_id: dict[str, dict[str, Any]]
    mentor_by_id: dict[str, dict[str, Any]]
    mentor_index_by_id: dict[str, int]
    email_owner_by_value: dict[str, str]


def _deduplicated_parts(
    value: str, pattern: re.Pattern[str], *, maximum: int | None = None
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in pattern.split(value):
        normalized = normalize_text(part)
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
        if maximum is not None and len(result) >= maximum:
            break
    return result


def _blocked_scopes(data: RepositoryData, user_id: int) -> set[str]:
    scopes: set[str] = set()
    for item in data.blocked.get("blocked", []):
        if item.get("github_user_id") == user_id:
            scopes.update(str(scope) for scope in item.get("scopes", []))
    return scopes


def _resolve_organization(
    data: RepositoryData,
    *,
    organization_id: str,
    university: str,
    school: str,
    department: str,
) -> tuple[str | None, list[str]]:
    reasons: list[str] = []
    if organization_id:
        organization = data.registry.by_id.get(organization_id)
        if organization is None or organization.get("status") != "active":
            return None, ["unknown_organization_id"]
        return organization_id, reasons

    university_match = data.registry.match(university, parent_id=None)
    if university_match.status != "matched" or university_match.organization_id is None:
        return None, [f"{university_match.status}_university"]
    resolved = university_match.organization_id
    if school:
        school_match = data.registry.match(school, parent_id=resolved)
        if school_match.status != "matched" or school_match.organization_id is None:
            return None, [f"{school_match.status}_school"]
        resolved = school_match.organization_id
    if department:
        department_match = data.registry.match(department, parent_id=resolved)
        if department_match.status != "matched" or department_match.organization_id is None:
            return None, [f"{department_match.status}_department"]
        resolved = department_match.organization_id
    return resolved, reasons


def _candidate_from_sections(
    data: RepositoryData,
    sections: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    name = normalize_text(sections["导师姓名"])
    email = normalize_email(sections["公开工作邮箱"])
    source_url = normalized_web_url(sections["官方证据页面"])
    profile_url = normalized_web_url(sections["官方个人主页"])
    if not name:
        raise SubmissionError("导师姓名不能为空")
    if not is_valid_email(email):
        raise SubmissionError("公开工作邮箱格式无效")
    if is_generic_email(email):
        raise SubmissionError("通用邮箱不能作为导师主邮箱投稿")
    if source_url is None:
        raise SubmissionError("官方证据页面必须是安全的 HTTP 或 HTTPS URL")
    if sections["官方个人主页"] and profile_url is None:
        raise SubmissionError("官方个人主页必须是安全的 HTTP 或 HTTPS URL")

    organization_id, reasons = _resolve_organization(
        data,
        organization_id=normalize_text(sections["社区机构 ID"]),
        university=normalize_text(sections["学校正式名称"]),
        school=normalize_text(sections["学院或研究院正式名称"]),
        department=normalize_text(sections["系所或中心"]),
    )
    if organization_id is not None and not data.registry.url_is_approved(
        source_url, organization_id
    ):
        reasons.append("unapproved_source_domain")

    title_text = normalize_text(sections["职称"])
    title = title_text if title_text in SUPPORTED_TITLES else None
    payload = {
        "name": name,
        "email": email,
        "organization_id": organization_id,
        "submitted_university": normalize_text(sections["学校正式名称"]) or None,
        "submitted_school": normalize_text(sections["学院或研究院正式名称"]) or None,
        "submitted_department": normalize_text(sections["系所或中心"]) or None,
        "title": title,
        "research_directions": _deduplicated_parts(
            sections["研究方向"],
            SPLIT_DIRECTIONS,
        ),
        "recent_papers": _deduplicated_parts(
            sections["近期或代表论文"],
            SPLIT_PAPERS,
            maximum=8,
        ),
        "profile_url": profile_url,
        "source_url": source_url,
        "mentor_status": "active",
    }
    return payload, reasons


def candidate_from_package_record(
    data: RepositoryData,
    record: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    sections = {
        "导师姓名": record["name"],
        "公开工作邮箱": record["email"],
        "社区机构 ID": "",
        "学校正式名称": record["university"],
        "学院或研究院正式名称": record["school"],
        "系所或中心": record["department"],
        "职称": record["title"],
        "研究方向": record["research_direction"],
        "近期或代表论文": record["recent_papers"],
        "官方个人主页": record["profile_url"],
        "官方证据页面": record["source_url"],
        "投稿确认": "我确认",
    }
    return _candidate_from_sections(data, sections)


def _match_mentor(
    data: RepositoryData, payload: dict[str, Any]
) -> tuple[str, str | None, list[str]]:
    email_matches: list[dict[str, Any]] = []
    for mentor in data.mentors:
        if any(
            contact.get("normalized_value") == payload["email"]
            and contact.get("status") in {"current", "former"}
            for contact in mentor.get("contacts", [])
        ):
            email_matches.append(mentor)
    if len(email_matches) > 1:
        return "ambiguous", None, ["email_matches_multiple_mentors"]
    if email_matches:
        mentor = email_matches[0]
        reasons: list[str] = []
        names = {normalize_name_key(item.get("value")) for item in mentor.get("names", [])}
        if normalize_name_key(payload["name"]) not in names:
            reasons.append("email_name_conflict")
        current_orgs = {
            item.get("organization_id")
            for item in mentor.get("affiliations", [])
            if item.get("status") == "current"
        }
        if payload.get("organization_id") not in current_orgs:
            reasons.append("email_organization_conflict")
        return ("conflict" if reasons else "matched_email"), mentor["id"], reasons

    profile_url = payload.get("profile_url")
    if profile_url:
        profile_matches = [
            mentor
            for mentor in data.mentors
            if any(
                profile.get("url") == profile_url and profile.get("status") == "current"
                for profile in mentor.get("profiles", [])
            )
        ]
        if len(profile_matches) > 1:
            return "ambiguous", None, ["profile_matches_multiple_mentors"]
        if profile_matches:
            mentor = profile_matches[0]
            names = {normalize_name_key(item.get("value")) for item in mentor.get("names", [])}
            if normalize_name_key(payload["name"]) not in names:
                return "conflict", mentor["id"], ["profile_name_conflict"]
            return "matched_profile", mentor["id"], ["new_email_requires_manual_review"]
    return "new", None, []


def build_mentor_proposal(
    data: RepositoryData,
    event: GitHubIssueEvent,
    actor: GitHubActor,
    *,
    submitted: dict[str, Any],
    review_reasons: list[str],
    proposal_id: str,
    batch_row: int | None = None,
) -> dict[str, Any]:
    if event.author_id != actor.user_id:
        raise SubmissionError("GitHub API 用户 ID 与 Issue 作者 ID 不一致")
    if event.author_login.casefold() != actor.login.casefold():
        raise SubmissionError("GitHub API login 与 Issue 作者不一致")
    if "contribute" in _blocked_scopes(data, actor.user_id):
        raise SubmissionError("该 GitHub 用户已被禁止投稿")
    match_status, target_mentor_id, match_reasons = _match_mentor(data, submitted)
    review_reasons = [*review_reasons, *match_reasons]

    age_days = account_age_days(actor, event.created_at)
    if actor.user_type != "User":
        review_reasons.append("unsupported_github_user_type")
    if age_days < data.policy["minimum_auto_merge_account_age_days"]:
        review_reasons.append("account_too_young")
    if match_status in {"ambiguous", "conflict", "matched_profile"}:
        review_reasons.append("identity_requires_manual_review")
    if not data.policy.get("automation", {}).get("auto_merge_enabled", False):
        review_reasons.append("auto_merge_disabled")

    review_reasons = list(dict.fromkeys(review_reasons))
    issue = {"number": event.number, "url": event.url}
    if batch_row is not None:
        issue["batch_row"] = batch_row
    proposal = {
        "schema_version": 1,
        "id": proposal_id,
        "kind": "mentor_contribution",
        "issue": issue,
        "contributor": {
            "github_user_id": actor.user_id,
            "github_login_at_submission": event.author_login,
            "github_user_type": actor.user_type,
            "submitted_at": event.created_at.isoformat().replace("+00:00", "Z"),
            "account_created_at": actor.created_at.isoformat().replace("+00:00", "Z"),
            "account_age_days_at_submission": age_days,
        },
        "submitted": submitted,
        "accepted": copy.deepcopy(submitted),
        "target_mentor_id": target_mentor_id,
        "match_status": match_status,
        "review_reasons": review_reasons,
        "auto_eligible": not review_reasons,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    schema = load_json(data.root / "schemas" / "proposal.schema.json")
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(proposal)
    )
    if errors:
        raise SubmissionError(f"生成的审核提案无效：{errors[0].message}")
    return proposal


def create_mentor_proposal(
    root: Path,
    event: GitHubIssueEvent,
    actor: GitHubActor,
    *,
    output_directory: Path,
) -> ProposalResult:
    data = load_repository(root, validate=True)
    sections = parse_issue_form(event.body, SINGLE_FORM_LABELS)
    if "我确认" not in sections["投稿确认"]:
        raise SubmissionError("投稿确认未完成")
    submitted, review_reasons = _candidate_from_sections(data, sections)
    proposal = build_mentor_proposal(
        data,
        event,
        actor,
        submitted=submitted,
        review_reasons=review_reasons,
        proposal_id=f"proposal_issue_{event.number}",
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"issue-{event.number}.json"
    write_json_atomic(path, proposal)
    return ProposalResult(proposal=proposal, path=path)


def _claim_payload(
    proposal: dict[str, Any], *, mentor_id: str, moderator_id: int | None
) -> dict[str, Any]:
    contributor = proposal["contributor"]
    issue = proposal["issue"]
    claim_id = stable_proposal_entity_id("claim", proposal, "claim")
    accepted = copy.deepcopy(proposal["accepted"])
    if accepted.get("organization_id") is None:
        raise SubmissionError("审核后的 organization_id 不能为空")
    source_fields = [
        field
        for field in [
            "name",
            "email",
            "organization_id",
            "title",
            "research_directions",
            "recent_papers",
            "profile_url",
        ]
        if accepted.get(field) not in (None, "", [])
    ]
    automatic = proposal.get("auto_eligible") is True and accepted == proposal["submitted"]
    mode = "automatic" if automatic else "manual"
    return {
        "schema_version": 1,
        "id": claim_id,
        "mentor_id": mentor_id,
        "status": "accepted",
        "contributor": {
            "github_user_id": contributor["github_user_id"],
            "github_login_at_submission": contributor["github_login_at_submission"],
            "github_user_type": contributor["github_user_type"],
            "issue_number": issue["number"],
            "issue_url": issue["url"],
            "submitted_at": contributor["submitted_at"],
            "account_created_at": contributor["account_created_at"],
            "account_age_days_at_submission": contributor["account_age_days_at_submission"],
        },
        "submitted": copy.deepcopy(proposal["submitted"]),
        "accepted": accepted,
        "evidence": [
            {
                "fields": source_fields,
                "source_url": accepted["source_url"],
                "observed_at": contributor["submitted_at"],
            }
        ],
        "moderation": {
            "mode": mode,
            "policy_version": 1,
            "normalized_fields": [
                key for key, value in proposal["submitted"].items() if accepted.get(key) != value
            ],
            "moderator_github_user_id": None if automatic else moderator_id,
            "reason": "GitHub PR merge approval"
            if not automatic and moderator_id is not None
            else None,
            "decision_at": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        },
        "revocation_reason": None,
        "revoked_at": None,
    }


def _new_mentor(proposal: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    accepted = claim["accepted"]
    claim_id = claim["id"]
    mentor_id = claim["mentor_id"]
    observed_at = claim["contributor"]["submitted_at"]
    affiliation_id = stable_proposal_entity_id("aff", proposal, "affiliation")
    profiles = []
    if accepted.get("profile_url"):
        profiles.append(
            {
                "url": accepted["profile_url"],
                "status": "current",
                "affiliation_id": affiliation_id,
                "observed_at": observed_at,
                "claim_ids": [claim_id],
            }
        )
    field_provenance = {
        "name": [claim_id],
        "email": [claim_id],
        "affiliations": [claim_id],
        "status": [claim_id],
    }
    for key in ["title", "research_directions", "recent_papers", "profile_url"]:
        if accepted.get(key) not in (None, "", []):
            field_provenance[key] = [claim_id]
    return {
        "schema_version": 1,
        "id": mentor_id,
        "status": "active",
        "status_reason": None,
        "status_source_url": accepted["source_url"],
        "status_observed_at": observed_at,
        "names": [
            {
                "value": accepted["name"],
                "kind": "native",
                "is_primary": True,
                "claim_ids": [claim_id],
            }
        ],
        "contacts": [
            {
                "type": "email",
                "value": accepted["email"],
                "normalized_value": accepted["email"],
                "status": "current",
                "is_primary": True,
                "affiliation_id": affiliation_id,
                "source_url": accepted["source_url"],
                "observed_at": observed_at,
                "claim_ids": [claim_id],
            }
        ],
        "affiliations": [
            {
                "id": affiliation_id,
                "organization_id": accepted["organization_id"],
                "status": "current",
                "is_primary": True,
                "title": accepted.get("title"),
                "started_at": None,
                "ended_at": None,
                "source_url": accepted["source_url"],
                "observed_at": observed_at,
                "claim_ids": [claim_id],
            }
        ],
        "profiles": profiles,
        "title": accepted.get("title"),
        "research_directions": accepted.get("research_directions", []),
        "recent_papers": accepted.get("recent_papers", []),
        "claim_ids": [claim_id],
        "resolution_ids": [],
        "field_provenance": field_provenance,
        "created_at": observed_at,
        "updated_at": observed_at,
        "last_verified_at": observed_at,
    }


def _append_support(
    mentor: dict[str, Any],
    claim: dict[str, Any],
) -> dict[str, Any]:
    updated = copy.deepcopy(mentor)
    accepted = claim["accepted"]
    claim_id = claim["id"]
    observed_at = claim["contributor"]["submitted_at"]
    primary_name = next(item for item in updated["names"] if item["is_primary"])
    if normalize_name_key(primary_name["value"]) != normalize_name_key(accepted["name"]):
        raise SubmissionError("审核后的姓名与目标导师主要姓名冲突")
    contact = next(
        (
            item
            for item in updated["contacts"]
            if item["normalized_value"] == accepted["email"]
            and item["status"] in {"current", "former"}
        ),
        None,
    )
    if contact is None:
        raise SubmissionError("现有导师不包含审核后的邮箱；邮箱变更需要专门纠错流程")
    affiliation = next(
        (
            item
            for item in updated["affiliations"]
            if item["organization_id"] == accepted["organization_id"]
            and item["status"] == "current"
        ),
        None,
    )
    if affiliation is None:
        raise SubmissionError("现有导师不包含审核后的当前机构；任职变更需要专门纠错流程")

    for nested in (primary_name, contact, affiliation):
        if claim_id not in nested["claim_ids"]:
            nested["claim_ids"].append(claim_id)
    updated["field_provenance"].setdefault("name", []).append(claim_id)
    updated["field_provenance"].setdefault("email", []).append(claim_id)
    updated["field_provenance"].setdefault("affiliations", []).append(claim_id)

    scalar_fields = ["title", "research_directions", "recent_papers"]
    for field in scalar_fields:
        incoming = accepted.get(field)
        current = updated.get(field)
        if incoming in (None, "", []):
            continue
        if current in (None, "", []):
            updated[field] = copy.deepcopy(incoming)
        elif current != incoming:
            raise SubmissionError(f"审核后的字段 {field} 与目标导师当前值冲突")
        updated["field_provenance"].setdefault(field, []).append(claim_id)

    profile_url = accepted.get("profile_url")
    if profile_url:
        profile = next((item for item in updated["profiles"] if item["url"] == profile_url), None)
        if profile is None and any(item["status"] == "current" for item in updated["profiles"]):
            raise SubmissionError("官方主页发生变化，需要专门纠错流程")
        if profile is None:
            profile = {
                "url": profile_url,
                "status": "current",
                "affiliation_id": affiliation["id"],
                "observed_at": observed_at,
                "claim_ids": [],
            }
            updated["profiles"].append(profile)
        if claim_id not in profile["claim_ids"]:
            profile["claim_ids"].append(claim_id)
        updated["field_provenance"].setdefault("profile_url", []).append(claim_id)

    updated["claim_ids"].append(claim_id)
    updated["updated_at"] = observed_at
    updated["last_verified_at"] = observed_at
    for provenance in updated["field_provenance"].values():
        provenance[:] = list(dict.fromkeys(provenance))
    return updated


def _finalization_context(
    data: RepositoryData,
    *,
    resolved_schema_root: Path,
) -> _FinalizationContext:
    mentor_by_id = {mentor["id"]: mentor for mentor in data.mentors}
    email_owner_by_value: dict[str, str] = {}
    for mentor in data.mentors:
        for contact in mentor.get("contacts", []):
            if contact.get("status") in {"current", "former"}:
                email_owner_by_value[contact["normalized_value"]] = mentor["id"]
    return _FinalizationContext(
        data=data,
        proposal_schema=load_json(resolved_schema_root / "schemas" / "proposal.schema.json"),
        claim_by_id={claim["id"]: claim for claim in data.claims},
        mentor_by_id=mentor_by_id,
        mentor_index_by_id={mentor["id"]: index for index, mentor in enumerate(data.mentors)},
        email_owner_by_value=email_owner_by_value,
    )


def _same_claim_ignoring_decision_time(
    existing_claim: dict[str, Any],
    generated_claim: dict[str, Any],
) -> bool:
    comparable = copy.deepcopy(generated_claim)
    existing_moderation = existing_claim.get("moderation")
    comparable_moderation = comparable.get("moderation")
    if isinstance(existing_moderation, dict) and isinstance(comparable_moderation, dict):
        comparable_moderation["decision_at"] = existing_moderation.get("decision_at")
    return existing_claim == comparable


def _finalize_proposal_in_context(
    context: _FinalizationContext,
    proposal_path: Path,
    *,
    moderator_github_user_id: int | None,
) -> tuple[Path, Path]:
    data = context.data
    proposal = load_json(proposal_path)
    errors = list(
        Draft202012Validator(
            context.proposal_schema,
            format_checker=FormatChecker(),
        ).iter_errors(proposal)
    )
    if errors:
        raise SubmissionError(f"审核提案无效：{errors[0].message}")
    user_id = proposal["contributor"]["github_user_id"]
    if "contribute" in _blocked_scopes(data, user_id):
        raise SubmissionError("提案贡献者已被封禁")
    accepted = proposal["accepted"]
    organization_id = accepted.get("organization_id")
    if organization_id not in data.registry.by_id:
        raise SubmissionError("审核后的机构不存在")
    if not data.registry.url_is_approved(accepted["source_url"], organization_id):
        raise SubmissionError("审核后的来源 URL 不属于批准机构域名")
    if not is_valid_email(accepted["email"]) or is_generic_email(accepted["email"]):
        raise SubmissionError("审核后的邮箱无效或属于通用邮箱")

    target_mentor_id = proposal.get("target_mentor_id")
    if target_mentor_id is None:
        mentor_id = proposed_mentor_id(proposal)
    else:
        mentor_id = target_mentor_id
        if mentor_id not in context.mentor_by_id:
            raise SubmissionError("目标导师不存在")

    claim = _claim_payload(proposal, mentor_id=mentor_id, moderator_id=moderator_github_user_id)
    existing_claim = context.claim_by_id.get(claim["id"])
    if existing_claim is not None:
        if _same_claim_ignoring_decision_time(existing_claim, claim):
            return data.claim_paths[claim["id"]], data.mentor_paths[mentor_id]
        raise SubmissionError("相同 Issue 的 Claim 已存在但内容不同")
    if target_mentor_id is None and accepted["email"] in context.email_owner_by_value:
        raise SubmissionError("新导师邮箱已被现有实体占用，必须重新审核目标 mentor_id")

    if target_mentor_id is None:
        mentor = _new_mentor(proposal, claim)
        mentor_path = data.root / "records" / "mentors" / f"{mentor_id}.json"
    else:
        existing_mentor = context.mentor_by_id[mentor_id]
        mentor = _append_support(existing_mentor, claim)
        mentor_path = data.mentor_paths[mentor_id]
    claim_path = data.root / "claims" / str(user_id) / f"{claim['id']}.json"
    write_json_atomic(claim_path, claim)
    write_json_atomic(mentor_path, mentor)

    context.claim_by_id[claim["id"]] = claim
    data.claims.append(claim)
    data.claim_paths[claim["id"]] = claim_path
    if target_mentor_id is None:
        context.mentor_index_by_id[mentor_id] = len(data.mentors)
        data.mentors.append(mentor)
        data.mentor_paths[mentor_id] = mentor_path
        context.email_owner_by_value[accepted["email"]] = mentor_id
    else:
        data.mentors[context.mentor_index_by_id[mentor_id]] = mentor
    context.mentor_by_id[mentor_id] = mentor
    return claim_path, mentor_path


def finalize_proposal(
    root: Path,
    proposal_path: Path,
    *,
    moderator_github_user_id: int | None,
    schema_root: Path | None = None,
) -> tuple[Path, Path]:
    resolved_schema_root = (schema_root or root).resolve()
    data = load_repository(root, validate=True, schema_root=resolved_schema_root)
    context = _finalization_context(data, resolved_schema_root=resolved_schema_root)
    result = _finalize_proposal_in_context(
        context,
        proposal_path,
        moderator_github_user_id=moderator_github_user_id,
    )
    load_repository(root, validate=True, schema_root=resolved_schema_root)
    return result


def check_proposal(
    root: Path,
    proposal_path: Path,
    *,
    schema_root: Path | None = None,
) -> None:
    resolved_root = root.resolve()
    resolved_proposal = proposal_path.resolve()
    try:
        relative_proposal = resolved_proposal.relative_to(resolved_root)
    except ValueError:
        relative_proposal = Path("proposals") / resolved_proposal.name
    with tempfile.TemporaryDirectory(prefix="mentor-data-proposal-") as temporary:
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
        finalize_proposal(
            rehearsal_root,
            rehearsal_proposal,
            moderator_github_user_id=1,
            schema_root=schema_root,
        )


def proposal_paths(root: Path) -> list[Path]:
    directory = root.resolve() / "proposals"
    if not directory.exists():
        return []
    return sorted(path for path in directory.rglob("*.json") if path.is_file())


def finalize_proposal_set(
    root: Path,
    paths: list[Path],
    *,
    moderator_github_user_id: int | None,
) -> list[tuple[Path, Path]]:
    if not paths:
        raise SubmissionError("没有找到待处理的导师提案")
    resolved_schema_root = root.resolve()
    data = load_repository(root, validate=True, schema_root=resolved_schema_root)
    context = _finalization_context(data, resolved_schema_root=resolved_schema_root)
    results: list[tuple[Path, Path]] = []
    for path in sorted(paths):
        results.append(
            _finalize_proposal_in_context(
                context,
                path,
                moderator_github_user_id=moderator_github_user_id,
            )
        )
    load_repository(root, validate=True, schema_root=resolved_schema_root)
    return results


def check_proposal_set(
    root: Path,
    paths: list[Path] | None = None,
    *,
    schema_root: Path | None = None,
) -> None:
    resolved_root = root.resolve()
    resolved_paths = [path.resolve() for path in (paths or proposal_paths(resolved_root))]
    if not resolved_paths:
        return
    with tempfile.TemporaryDirectory(prefix="mentor-data-proposal-set-") as temporary:
        rehearsal_root = Path(temporary) / "repository"
        shutil.copytree(
            resolved_root,
            rehearsal_root,
            ignore=shutil.ignore_patterns(".git", ".venv", "dist", ".work", "__pycache__"),
        )
        rehearsal_paths: list[Path] = []
        for index, source in enumerate(resolved_paths, start=1):
            try:
                relative = source.relative_to(resolved_root)
            except ValueError:
                relative = Path("proposals") / "external" / f"{index:04d}-{source.name}"
            destination = rehearsal_root / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            rehearsal_paths.append(destination)
        resolved_schema_root = (schema_root or rehearsal_root).resolve()
        data = load_repository(
            rehearsal_root,
            validate=True,
            schema_root=resolved_schema_root,
        )
        context = _finalization_context(data, resolved_schema_root=resolved_schema_root)
        for rehearsal_path in sorted(rehearsal_paths):
            _finalize_proposal_in_context(
                context,
                rehearsal_path,
                moderator_github_user_id=1,
            )
            rehearsal_path.unlink()
        load_repository(
            rehearsal_root,
            validate=True,
            schema_root=resolved_schema_root,
        )
