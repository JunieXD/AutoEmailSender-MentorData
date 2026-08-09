from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import RepositoryValidationError, SubmissionError
from .github_events import GITHUB_LOGIN_PATTERN, parse_datetime
from .identifiers import proposed_mentor_id, stable_proposal_entity_id
from .io_utils import json_bytes, load_json, write_json_atomic, write_yaml_atomic
from .normalization import (
    hostname_for_url,
    normalize_email,
    normalize_name_key,
    normalize_organization_key,
    normalize_text,
    normalized_web_url,
)
from .organizations import OrganizationRegistry
from .proposals import check_proposal_set
from .repository import RepositoryData, load_repository, validate_repository_data

if TYPE_CHECKING:
    from .batch import BatchProposalResult
    from .github_events import GitHubIssueEvent


REVIEW_COMMENT_MARKER = "<!-- mentor-data-organization-review:v1 -->"
GITHUB_COMMENT_CHARACTER_LIMIT = 65_536
REVIEW_BRANCH_PATTERN = re.compile(r"^batch/issue-(?P<issue>[1-9][0-9]*)$")
PROPOSAL_DIRECTORY_PATTERN = re.compile(r"^proposals/batch-issue-(?P<issue>[1-9][0-9]*)$")
DOMAIN_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
LEVELS = ("university", "school", "department")
LEVEL_TYPES = {
    "university": {"university"},
    "school": {"school", "institute"},
    "department": {"department", "center", "laboratory"},
}
PATH_CORRECTION_KINDS = {
    "department_as_school",
    "department_as_institute",
    "custom",
}
PARENT_TYPES_BY_ORGANIZATION_TYPE = {
    "school": {"university"},
    "institute": {"university"},
    "department": {"school", "institute"},
    "center": {"school", "institute"},
    "laboratory": {"school", "institute"},
}
ORGANIZATION_REVIEW_REASONS = {
    "unknown_organization_id",
    "unapproved_source_domain",
    "unapproved_profile_domain",
    "unmatched_university",
    "ambiguous_university",
    "unmatched_school",
    "ambiguous_school",
    "unmatched_department",
    "ambiguous_department",
}
AFFILIATION_IDENTITY_REASONS = {
    "email_organization_conflict",
    "identity_requires_manual_review",
}
UNSUPPORTED_AFFILIATION_IDENTITY_REASONS = {
    "email_name_conflict",
    "profile_name_conflict",
    "email_matches_multiple_mentors",
    "profile_matches_multiple_mentors",
    "former_email_requires_correction",
}
TRUSTED_REVIEW_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


@dataclass(frozen=True, slots=True)
class ReviewComment:
    pull_request_number: int
    comment_id: int
    reviewer_id: int
    reviewer_login: str
    author_association: str
    created_at: datetime
    decision: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReviewPull:
    number: int
    issue_number: int
    head_ref: str
    repository: str


@dataclass(frozen=True, slots=True)
class AppliedOrganizationReview:
    remaining_proposals: int
    mapped_proposals: int
    rejected_proposals: int
    created_organizations: int
    updated_organizations: int
    invalid_rows: int
    ready_for_finalization: bool
    finalization_error: str | None


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_schema(root: Path, schema_name: str, value: Any, label: str) -> None:
    schema = load_json(root / "schemas" / schema_name)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise SubmissionError(f"{label}格式无效（{location}）：{errors[0].message}")


def _registry_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(json_bytes(document, pretty=False)).hexdigest()


def _group_id(submitted: dict[str, str | None]) -> str:
    seed = "\n".join(normalize_organization_key(submitted.get(level)) for level in LEVELS)
    return f"org_group_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _submitted_path_key(submitted: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(
        normalize_organization_key(submitted.get(level)) for level in LEVELS
    )  # type: ignore[return-value]


def _active_organization_or_successor(
    registry: OrganizationRegistry,
    organization_id: str,
) -> dict[str, Any] | None:
    current_id: str | None = organization_id
    seen: set[str] = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        organization = registry.by_id.get(current_id)
        if organization is None:
            return None
        if organization.get("status") == "active":
            return organization
        successor_id = organization.get("successor_id")
        current_id = successor_id if isinstance(successor_id, str) else None
    return None


def _historical_path_correction(
    data: RepositoryData,
    submitted: dict[str, str | None],
) -> dict[str, Any] | None:
    path_key = _submitted_path_key(submitted)
    candidates: list[tuple[datetime, str, dict[str, Any]]] = []
    for resolution in data.organization_review_resolutions:
        decided_at = resolution.get("decided_at")
        if not isinstance(decided_at, str):
            continue
        for correction in resolution.get("path_corrections", []):
            if _submitted_path_key(correction.get("submitted", {})) != path_key:
                continue
            candidates.append(
                (
                    parse_datetime(decided_at),
                    str(resolution.get("id", "")),
                    correction,
                )
            )
    if not candidates:
        return None
    correction = max(candidates, key=lambda item: (item[0], item[1]))[2]
    target_id = correction.get("target_organization_id")
    if not isinstance(target_id, str):
        return None
    target = _active_organization_or_successor(data.registry, target_id)
    if target is None:
        return None
    kind = correction.get("kind")
    reason = normalize_text(correction.get("reason"))
    if kind not in PATH_CORRECTION_KINDS or not reason:
        return None
    return {
        "kind": kind,
        "target_organization_id": target["id"],
        "source": "history",
        "reason": reason,
    }


def _heuristic_path_correction(
    data: RepositoryData,
    submitted: dict[str, str | None],
) -> dict[str, Any] | None:
    department = normalize_text(submitted.get("department"))
    school = normalize_text(submitted.get("school"))
    if (
        not department
        or normalize_organization_key(department) == normalize_organization_key(school)
    ):
        return None
    if department.endswith("研究院"):
        kind = "department_as_institute"
        expected_type = "institute"
        reason = "系所字段以“研究院”结尾，疑似同校平级研究院"
    elif department.endswith("学院"):
        kind = "department_as_school"
        expected_type = "school"
        reason = "系所字段以“学院”结尾，疑似同校平级学院"
    else:
        return None

    target_id: str | None = None
    university = data.registry.match(
        normalize_text(submitted.get("university")),
        parent_id=None,
    )
    if university.status == "matched" and university.organization_id is not None:
        sibling = data.registry.match(department, parent_id=university.organization_id)
        if sibling.status == "matched" and sibling.organization_id is not None:
            organization = data.registry.by_id[sibling.organization_id]
            if organization.get("type") == expected_type:
                target_id = sibling.organization_id
    return {
        "kind": kind,
        "target_organization_id": target_id,
        "source": "heuristic",
        "reason": reason,
    }


def _suggested_path_correction(
    data: RepositoryData,
    submitted: dict[str, str | None],
) -> dict[str, Any] | None:
    return _historical_path_correction(data, submitted) or _heuristic_path_correction(
        data,
        submitted,
    )


def _organization_options(registry: OrganizationRegistry) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for organization in sorted(registry.organizations, key=lambda item: item["id"]):
        if organization.get("status") != "active":
            continue
        lineage = registry.lineage(organization["id"])
        options.append(
            {
                "id": organization["id"],
                "type": organization["type"],
                "canonical_name": organization["canonical_name"],
                "parent_id": organization.get("parent_id"),
                "aliases": list(organization.get("aliases", [])),
                "official_urls": list(organization.get("official_urls", [])),
                "approved_domains": sorted(registry.approved_domains(organization["id"])),
                "lineage_ids": [item["id"] for item in lineage],
                "lineage_names": [item["canonical_name"] for item in lineage],
            }
        )
    return options


def _supported_affiliation_identity(
    data: RepositoryData,
    proposal: dict[str, Any],
    *,
    pending_proposals: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any] | None:
    """Return the reviewer-safe identity snapshot for an email/org conflict.

    The organization review can only resolve a known email match whose primary name is
    unchanged. Other identity conflicts deliberately stay outside this lightweight flow.
    """

    target_mentor_id = proposal.get("target_mentor_id")
    reasons = set(proposal.get("review_reasons", []))
    if (
        proposal.get("match_status") != "conflict"
        or not isinstance(target_mentor_id, str)
        or not AFFILIATION_IDENTITY_REASONS.issubset(reasons)
        or reasons & UNSUPPORTED_AFFILIATION_IDENTITY_REASONS
    ):
        return None
    mentor = next((item for item in data.mentors if item.get("id") == target_mentor_id), None)
    if mentor is None:
        pending = next(
            (
                item
                for item in pending_proposals
                if item.get("target_mentor_id") is None
                and proposed_mentor_id(item) == target_mentor_id
            ),
            None,
        )
        if pending is not None:
            pending_accepted = pending["accepted"]
            pending_organization_id = pending_accepted.get("organization_id")
            observed_at = pending["contributor"]["submitted_at"]
            source_url = (
                pending_accepted.get("profile_url")
                or pending_accepted["source_url"]
            )
            affiliation_id = stable_proposal_entity_id(
                "aff", pending, "affiliation"
            )
            submitted_path = " / ".join(
                value
                for value in (
                    pending_accepted.get("submitted_university"),
                    pending_accepted.get("submitted_school"),
                    pending_accepted.get("submitted_department"),
                )
                if isinstance(value, str) and value
            )
            mentor = {
                "id": target_mentor_id,
                "names": [
                    {"value": pending_accepted["name"], "is_primary": True}
                ],
                "contacts": [
                    {
                        "normalized_value": pending_accepted["email"],
                        "status": "current",
                        "is_primary": True,
                        "affiliation_id": affiliation_id,
                    }
                ],
                "affiliations": [
                    {
                        "id": affiliation_id,
                        "organization_id": (
                            pending_organization_id
                            if isinstance(pending_organization_id, str)
                            else None
                        ),
                        "organization_label": submitted_path or "机构待审核",
                        "status": "current",
                        "is_primary": True,
                        "title": pending_accepted.get("title"),
                        "source_url": source_url,
                        "observed_at": observed_at,
                    }
                ],
            }
    if mentor is None:
        return None
    accepted = proposal.get("accepted", {})
    submitted_name = accepted.get("name")
    submitted_email = accepted.get("email")
    primary_name = next((item for item in mentor.get("names", []) if item.get("is_primary")), None)
    matching_contact = next(
        (
            item
            for item in mentor.get("contacts", [])
            if item.get("status") == "current"
            and item.get("normalized_value") == normalize_email(str(submitted_email or ""))
        ),
        None,
    )
    current_affiliations = [
        item for item in mentor.get("affiliations", []) if item.get("status") == "current"
    ]
    if (
        primary_name is None
        or matching_contact is None
        or not isinstance(submitted_name, str)
        or normalize_name_key(primary_name.get("value")) != normalize_name_key(submitted_name)
        or not current_affiliations
    ):
        return None
    return {
        "requires_resolution": True,
        "target_mentor_id": target_mentor_id,
        "match_status": "conflict",
        "review_reasons": sorted(AFFILIATION_IDENTITY_REASONS),
        "mentor": {
            "id": mentor["id"],
            "name": primary_name["value"],
            "email": matching_contact["normalized_value"],
            "affiliations": [
                {
                    "id": affiliation["id"],
                    "organization_id": affiliation["organization_id"],
                    "status": "current",
                    "is_primary": affiliation["is_primary"],
                    "title": affiliation.get("title"),
                    "source_url": affiliation["source_url"],
                    "observed_at": affiliation["observed_at"],
                    **(
                        {"organization_label": affiliation["organization_label"]}
                        if affiliation.get("organization_label")
                        else {}
                    ),
                }
                for affiliation in sorted(current_affiliations, key=lambda item: item["id"])
            ],
        },
    }


def _manifest_row(
    data: RepositoryData,
    proposal: dict[str, Any],
    *,
    pending_proposals: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    submitted = proposal["submitted"]
    row = {
        "proposal_id": proposal["id"],
        "batch_row": proposal["issue"]["batch_row"],
        "name": submitted["name"],
        "email": submitted["email"],
        "profile_url": submitted.get("profile_url"),
        "source_url": submitted["source_url"],
    }
    identity = _supported_affiliation_identity(
        data,
        proposal,
        pending_proposals=pending_proposals,
    )
    if identity is not None:
        row["identity"] = identity
    return row


def create_organization_review_manifest(
    root: Path,
    event: GitHubIssueEvent,
    result: BatchProposalResult,
    *,
    proposal_directory: str,
    output_path: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    match = PROPOSAL_DIRECTORY_PATTERN.fullmatch(proposal_directory)
    if match is None or int(match.group("issue")) != event.number:
        raise SubmissionError("机构审核清单的提案目录与 Issue 不一致")

    data = load_repository(root, validate=True)
    grouped: dict[str, dict[str, Any]] = {}
    for proposal in result.proposals:
        submitted_payload = proposal["submitted"]
        submitted = {
            "university": submitted_payload.get("submitted_university"),
            "school": submitted_payload.get("submitted_school"),
            "department": submitted_payload.get("submitted_department"),
        }
        group_id = _group_id(submitted)
        group = grouped.setdefault(
            group_id,
            {
                "id": group_id,
                "submitted": submitted,
                "rows": [],
                "source_domains": set(),
                "source_urls": set(),
                "suggested_organization_ids": set(),
                "review_reasons": set(),
            },
        )
        source_url = submitted_payload["source_url"]
        group["rows"].append(
            _manifest_row(data, proposal, pending_proposals=result.proposals)
        )
        group["source_domains"].add(hostname_for_url(source_url))
        group["source_urls"].add(source_url)
        profile_url = submitted_payload.get("profile_url")
        if profile_url:
            group["source_domains"].add(hostname_for_url(profile_url))
            group["source_urls"].add(profile_url)
        organization_id = proposal["accepted"].get("organization_id")
        if organization_id is not None:
            group["suggested_organization_ids"].add(organization_id)
        group["review_reasons"].update(proposal["review_reasons"])

    groups: list[dict[str, Any]] = []
    for group_id in sorted(grouped):
        group = grouped[group_id]
        suggested_ids = group.pop("suggested_organization_ids")
        groups.append(
            {
                **group,
                "rows": sorted(group["rows"], key=lambda item: item["batch_row"]),
                "source_domains": sorted(group["source_domains"]),
                "source_urls": sorted(group["source_urls"]),
                "suggested_organization_id": (
                    next(iter(suggested_ids)) if len(suggested_ids) == 1 else None
                ),
                "suggested_path_correction": _suggested_path_correction(
                    data,
                    group["submitted"],
                ),
                "review_reasons": sorted(group["review_reasons"]),
            }
        )

    manifest = {
        "schema_version": 1,
        "kind": "batch_organization_review",
        "issue": {"number": event.number, "url": event.url},
        "proposal_directory": proposal_directory,
        "generated_at": _iso_utc(generated_at or datetime.now(UTC)),
        "registry_sha256": _registry_digest(data.organizations_document),
        "groups": groups,
        "invalid_rows": [copy.deepcopy(item) for item in result.invalid_rows],
        "organizations": _organization_options(data.registry),
    }
    _validate_schema(root, "organization-review.schema.json", manifest, "机构审核清单")
    write_json_atomic(output_path, manifest)
    return manifest


def _parse_review_comment_payload(body: str) -> dict[str, Any]:
    if len(body) > GITHUB_COMMENT_CHARACTER_LIMIT:
        raise SubmissionError(
            f"机构审核评论超过 GitHub 的 {GITHUB_COMMENT_CHARACTER_LIMIT:,} 字符上限"
        )
    normalized_body = body.replace("\r\n", "\n")
    if "\r" in normalized_body:
        raise SubmissionError("机构审核评论包含不支持的换行符")
    if not normalized_body.startswith(REVIEW_COMMENT_MARKER):
        raise SubmissionError("评论不是机构审核指令")
    remainder = normalized_body[len(REVIEW_COMMENT_MARKER) :].strip()
    if not remainder.startswith("```json\n") or not remainder.endswith("```"):
        raise SubmissionError("机构审核评论必须包含唯一的 JSON 代码块")
    payload = remainder[len("```json\n") : -len("```")].strip()
    if "```" in payload:
        raise SubmissionError("机构审核评论包含多余代码块")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SubmissionError("机构审核评论不是有效 JSON") from error
    if not isinstance(value, dict):
        raise SubmissionError("机构审核决策必须是 JSON 对象")
    return value


def load_review_comment(root: Path, event_path: Path) -> ReviewComment:
    event = load_json(event_path)
    issue = event.get("issue")
    comment = event.get("comment")
    if not isinstance(issue, dict) or not isinstance(issue.get("pull_request"), dict):
        raise SubmissionError("机构审核指令必须发布在 Pull Request 评论中")
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
        raise SubmissionError("只有仓库所有者或受信任协作者可以应用机构审核")
    decision = _parse_review_comment_payload(str(comment.get("body") or ""))
    _validate_schema(
        root,
        "organization-review-decision.schema.json",
        decision,
        "机构审核决策",
    )
    if decision["pull_request_number"] != pull_request_number:
        raise SubmissionError("机构审核决策中的 Pull Request 编号不一致")
    return ReviewComment(
        pull_request_number=pull_request_number,
        comment_id=comment_id,
        reviewer_id=reviewer_id,
        reviewer_login=reviewer_login,
        author_association=association,
        created_at=parse_datetime(str(comment.get("created_at", ""))),
        decision=decision,
    )


def load_review_pull(
    pull_path: Path,
    *,
    expected_repository: str,
    expected_number: int,
) -> ReviewPull:
    pull = load_json(pull_path)
    number = pull.get("number")
    state = pull.get("state")
    head = pull.get("head")
    base = pull.get("base")
    if number != expected_number or state != "open":
        raise SubmissionError("机构审核只能应用到指定的开放 Pull Request")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise SubmissionError("Pull Request 元数据缺少 head/base")
    head_repository = head.get("repo")
    if (
        not isinstance(head_repository, dict)
        or head_repository.get("full_name") != expected_repository
    ):
        raise SubmissionError("机构审核不允许修改 fork 分支")
    if base.get("ref") != "main":
        raise SubmissionError("机构审核 Pull Request 必须以 main 为目标分支")
    head_ref = head.get("ref")
    if not isinstance(head_ref, str):
        raise SubmissionError("Pull Request head 分支无效")
    branch_match = REVIEW_BRANCH_PATTERN.fullmatch(head_ref)
    if branch_match is None:
        raise SubmissionError("机构审核只允许处理受信任的批量投稿内部分支")
    return ReviewPull(
        number=number,
        issue_number=int(branch_match.group("issue")),
        head_ref=head_ref,
        repository=expected_repository,
    )


def _normalize_domain(value: str) -> str:
    domain = normalize_text(value).lower().rstrip(".")
    if len(domain) > 253 or DOMAIN_PATTERN.fullmatch(domain) is None:
        raise SubmissionError(f"批准域名无效：{value}")
    return domain


def _proposed_organization_id(
    organization_type: str,
    canonical_name: str,
    parent_id: str | None,
) -> str:
    seed = f"{organization_type}\n{normalize_organization_key(canonical_name)}\n{parent_id or ''}"
    return f"org_auto_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _add_alias(
    organization: dict[str, Any],
    submitted_name: str,
    *,
    decided_at: str,
) -> bool:
    alias = normalize_text(submitted_name)
    if not alias:
        return False
    existing_names = [organization["canonical_name"], *organization.get("aliases", [])]
    alias_key = normalize_organization_key(alias)
    if alias_key in {normalize_organization_key(item) for item in existing_names}:
        return False
    organization.setdefault("aliases", []).append(alias)
    organization["updated_at"] = decided_at
    return True


def _find_exact_active_organization(
    organizations: list[dict[str, Any]],
    *,
    level: str,
    parent_id: str | None,
    name: str,
) -> dict[str, Any] | None:
    key = normalize_organization_key(name)
    matches = [
        organization
        for organization in organizations
        if organization.get("status") == "active"
        and organization.get("parent_id") == parent_id
        and organization.get("type") in LEVEL_TYPES[level]
        and key
        in {
            normalize_organization_key(value)
            for value in [
                organization.get("canonical_name", ""),
                *organization.get("aliases", []),
            ]
        }
    ]
    if len(matches) > 1:
        raise SubmissionError(f"同级存在多个同名机构，无法自动归类：{name}")
    return matches[0] if matches else None


def _merge_organization_metadata(
    organization: dict[str, Any],
    *,
    official_url: str | None,
    approved_domains: list[str],
    submitted_name: str,
    save_submitted_as_alias: bool,
    decided_at: str,
) -> bool:
    changed = False
    if official_url is not None and official_url not in organization["official_urls"]:
        organization["official_urls"].append(official_url)
        changed = True
    for domain in approved_domains:
        if domain not in organization["approved_domains"]:
            organization["approved_domains"].append(domain)
            changed = True
    if save_submitted_as_alias and _add_alias(
        organization,
        submitted_name,
        decided_at=decided_at,
    ):
        changed = True
    if changed:
        organization["updated_at"] = decided_at
    return changed


def _resolve_levels(
    organizations_document: dict[str, Any],
    organizations_by_id: dict[str, dict[str, Any]],
    submitted: dict[str, str | None],
    levels: list[dict[str, Any]],
    *,
    decided_at: str,
) -> tuple[str, set[str], set[str]]:
    if [item["level"] for item in levels] != list(LEVELS):
        raise SubmissionError("机构层级决策必须按学校、学院、系所完整排列")
    organizations = organizations_document["organizations"]
    parent_id: str | None = None
    target_id: str | None = None
    created_ids: set[str] = set()
    updated_ids: set[str] = set()
    skipped_parent = False

    for item in levels:
        level = item["level"]
        action = item["action"]
        submitted_name = normalize_text(submitted.get(level))
        if action == "skip":
            if level == "university":
                raise SubmissionError("学校层级不能跳过")
            if level == "school":
                skipped_parent = True
            continue
        if skipped_parent:
            raise SubmissionError("跳过学院后不能继续映射或新建系所")

        if action == "existing":
            organization_id = item.get("organization_id")
            organization = organizations_by_id.get(organization_id)
            if organization is None or organization.get("status") != "active":
                raise SubmissionError(f"所选机构不存在或不可用：{organization_id}")
            if organization["type"] not in LEVEL_TYPES[level]:
                raise SubmissionError(f"所选机构 {organization_id} 的类型不属于{level}")
            if organization.get("parent_id") != parent_id:
                raise SubmissionError(f"所选机构 {organization_id} 不属于已确认的上级机构")
            if item["save_submitted_as_alias"] and _add_alias(
                organization,
                submitted_name,
                decided_at=decided_at,
            ):
                updated_ids.add(organization_id)
            parent_id = organization_id
            target_id = organization_id
            continue

        if action != "create":
            raise SubmissionError(f"未知机构层级操作：{action}")
        organization_type = item.get("organization_type")
        if organization_type not in LEVEL_TYPES[level]:
            raise SubmissionError(f"新机构类型不属于{level}层级")
        canonical_name = normalize_text(item.get("canonical_name"))
        if not canonical_name:
            raise SubmissionError("新机构必须填写正式名称")
        official_url_value = normalize_text(item.get("official_url"))
        official_url = normalized_web_url(official_url_value)
        if official_url_value and official_url is None:
            raise SubmissionError("新机构官网必须是安全的 HTTP 或 HTTPS URL")
        approved_domains = sorted(
            {_normalize_domain(value) for value in item.get("approved_domains", [])}
        )
        exact_existing = _find_exact_active_organization(
            organizations,
            level=level,
            parent_id=parent_id,
            name=canonical_name,
        )
        if exact_existing is not None:
            if _merge_organization_metadata(
                exact_existing,
                official_url=official_url,
                approved_domains=approved_domains,
                submitted_name=submitted_name,
                save_submitted_as_alias=item["save_submitted_as_alias"],
                decided_at=decided_at,
            ):
                updated_ids.add(exact_existing["id"])
            parent_id = exact_existing["id"]
            target_id = exact_existing["id"]
            continue
        if level == "university" and official_url is None:
            raise SubmissionError("新学校必须填写安全的 HTTP 或 HTTPS 官网")
        organization_id = _proposed_organization_id(
            organization_type,
            canonical_name,
            parent_id,
        )
        existing = organizations_by_id.get(organization_id)
        if existing is None:
            aliases: list[str] = []
            if (
                item["save_submitted_as_alias"]
                and submitted_name
                and normalize_organization_key(submitted_name)
                != normalize_organization_key(canonical_name)
            ):
                aliases.append(submitted_name)
            organization = {
                "id": organization_id,
                "type": organization_type,
                "canonical_name": canonical_name,
                "parent_id": parent_id,
                "aliases": aliases,
                "official_urls": [official_url] if official_url is not None else [],
                "approved_domains": approved_domains,
                "status": "active",
                "successor_id": None,
                "created_at": decided_at,
                "updated_at": decided_at,
            }
            organizations.append(organization)
            organizations_by_id[organization_id] = organization
            created_ids.add(organization_id)
        else:
            expected = {
                "type": organization_type,
                "canonical_name": canonical_name,
                "parent_id": parent_id,
            }
            if any(existing.get(key) != value for key, value in expected.items()):
                raise SubmissionError(f"自动生成的机构 ID 与现有机构冲突：{organization_id}")
            organization = existing
            if _merge_organization_metadata(
                organization,
                official_url=official_url,
                approved_domains=approved_domains,
                submitted_name=submitted_name,
                save_submitted_as_alias=item["save_submitted_as_alias"],
                decided_at=decided_at,
            ):
                updated_ids.add(organization_id)
        parent_id = organization_id
        target_id = organization_id

    if target_id is None:
        raise SubmissionError("机构决策没有得到最终归属机构")
    return target_id, created_ids, updated_ids


def _validate_independent_creation_references(
    decision: dict[str, Any],
    manifest_groups: dict[str, dict[str, Any]],
) -> None:
    creations = decision.get("organization_creations", [])
    creation_by_id = {item["organization_id"]: item for item in creations}
    if len(creation_by_id) != len(creations):
        raise SubmissionError("独立新建机构包含重复 ID")
    referenced_ids: set[str] = set()
    for group_decision in decision["decisions"]:
        group = manifest_groups[group_decision["group_id"]]
        overrides = {
            override["proposal_id"]: override
            for override in group_decision["row_overrides"]
        }
        for override in overrides.values():
            target_id = override.get("organization_id")
            if override.get("action") == "map_existing" and isinstance(target_id, str):
                referenced_ids.add(target_id)
        target_id = group_decision.get("target_organization_id")
        if group_decision["action"] != "resolve" or not isinstance(target_id, str):
            continue
        if any(
            row["proposal_id"] not in overrides
            for row in group["rows"]
        ):
            referenced_ids.add(target_id)

    required_creation_ids: set[str] = set()
    pending = [item for item in referenced_ids if item in creation_by_id]
    while pending:
        organization_id = pending.pop()
        if organization_id in required_creation_ids:
            continue
        required_creation_ids.add(organization_id)
        parent_id = creation_by_id[organization_id].get("parent_id")
        if isinstance(parent_id, str) and parent_id in creation_by_id:
            pending.append(parent_id)
    unused = sorted(set(creation_by_id) - required_creation_ids)
    if unused:
        raise SubmissionError(f"独立新建机构没有被任何导师使用：{', '.join(unused)}")


def _apply_independent_organization_creations(
    organizations_document: dict[str, Any],
    organizations_by_id: dict[str, dict[str, Any]],
    creations: list[dict[str, Any]],
    *,
    decided_at: str,
) -> tuple[set[str], set[str]]:
    creation_by_id = {item["organization_id"]: item for item in creations}
    if len(creation_by_id) != len(creations):
        raise SubmissionError("独立新建机构包含重复 ID")
    for organization_id, item in creation_by_id.items():
        parent_id = item.get("parent_id")
        organization_type = item["organization_type"]
        canonical_name = normalize_text(item.get("canonical_name"))
        if not canonical_name:
            raise SubmissionError(f"新机构 {organization_id} 必须填写正式名称")
        expected_id = _proposed_organization_id(
            organization_type,
            canonical_name,
            parent_id,
        )
        if organization_id != expected_id:
            raise SubmissionError(f"独立新建机构 ID 与规范字段不一致：{organization_id}")
        if organization_type == "university" and parent_id is not None:
            raise SubmissionError(f"新学校 {organization_id} 不能设置上级机构")
        if organization_type != "university" and not isinstance(parent_id, str):
            raise SubmissionError(f"新机构 {organization_id} 必须选择上级机构")
        if (
            isinstance(parent_id, str)
            and parent_id not in organizations_by_id
            and parent_id not in creation_by_id
        ):
            raise SubmissionError(f"新机构 {organization_id} 的上级机构不存在：{parent_id}")
        if isinstance(parent_id, str):
            parent = organizations_by_id.get(parent_id) or creation_by_id.get(parent_id)
            parent_type = (
                parent.get("type")
                if parent is not None and "type" in parent
                else parent.get("organization_type") if parent is not None else None
            )
            allowed_parent_types = PARENT_TYPES_BY_ORGANIZATION_TYPE.get(
                organization_type,
                set(),
            )
            if parent_type not in allowed_parent_types:
                raise SubmissionError(
                    f"新机构 {organization_id} 的上级机构类型不正确：{parent_id}"
                )

    organizations = organizations_document["organizations"]
    pending = dict(creation_by_id)
    created_ids: set[str] = set()
    updated_ids: set[str] = set()
    while pending:
        progressed = False
        for organization_id in sorted(pending):
            item = pending[organization_id]
            parent_id = item.get("parent_id")
            if isinstance(parent_id, str) and parent_id in pending:
                continue
            parent = organizations_by_id.get(parent_id) if isinstance(parent_id, str) else None
            if parent is not None and parent.get("status") != "active":
                raise SubmissionError(f"新机构 {organization_id} 的上级机构不可用：{parent_id}")

            canonical_name = normalize_text(item["canonical_name"])
            organization_type = item["organization_type"]
            official_url_value = normalize_text(item.get("official_url"))
            official_url = normalized_web_url(official_url_value)
            if official_url_value and official_url is None:
                raise SubmissionError(f"新机构 {organization_id} 的官网地址无效")
            approved_domains = sorted(
                {_normalize_domain(value) for value in item.get("approved_domains", [])}
            )
            if organization_type == "university" and official_url is None:
                raise SubmissionError(f"新学校 {organization_id} 必须填写官方网站")
            if organization_type == "university" and not approved_domains:
                raise SubmissionError(f"新学校 {organization_id} 必须填写官方来源域名")

            same_name = [
                organization
                for organization in organizations
                if organization.get("status") == "active"
                and organization.get("parent_id") == parent_id
                and normalize_organization_key(organization.get("canonical_name"))
                == normalize_organization_key(canonical_name)
            ]
            if len(same_name) > 1:
                raise SubmissionError(f"新机构 {organization_id} 的同级正式名称存在歧义")
            if same_name and same_name[0]["id"] != organization_id:
                raise SubmissionError(
                    f"同级已存在正式名称为“{canonical_name}”的机构；请直接选择现有机构"
                )

            existing = organizations_by_id.get(organization_id)
            if existing is None:
                organization = {
                    "id": organization_id,
                    "type": organization_type,
                    "canonical_name": canonical_name,
                    "parent_id": parent_id,
                    "aliases": [],
                    "official_urls": [official_url] if official_url is not None else [],
                    "approved_domains": approved_domains,
                    "status": "active",
                    "successor_id": None,
                    "created_at": decided_at,
                    "updated_at": decided_at,
                }
                organizations.append(organization)
                organizations_by_id[organization_id] = organization
                created_ids.add(organization_id)
            else:
                expected = {
                    "type": organization_type,
                    "canonical_name": canonical_name,
                    "parent_id": parent_id,
                    "status": "active",
                }
                if any(existing.get(key) != value for key, value in expected.items()):
                    raise SubmissionError(f"独立新建机构与现有记录冲突：{organization_id}")
                if _merge_organization_metadata(
                    existing,
                    official_url=official_url,
                    approved_domains=approved_domains,
                    submitted_name=canonical_name,
                    save_submitted_as_alias=False,
                    decided_at=decided_at,
                ):
                    updated_ids.add(organization_id)

            registry = OrganizationRegistry(organizations)
            if official_url is not None and not registry.url_is_approved(
                official_url,
                organization_id,
            ):
                raise SubmissionError(f"新机构 {organization_id} 的官网不属于批准域名")
            pending.pop(organization_id)
            progressed = True
        if not progressed:
            raise SubmissionError("独立新建机构的上级关系存在循环")
    return created_ids, updated_ids


def _candidate_repository_data(
    source_data: RepositoryData,
    organizations_document: dict[str, Any],
    proposals_by_id: dict[str, dict[str, Any]],
    proposal_paths_by_id: dict[str, Path],
    proposal_directory: Path,
) -> RepositoryData:
    data = copy.deepcopy(source_data)
    data.organizations_document = organizations_document
    data.registry = OrganizationRegistry(organizations_document["organizations"])
    batch_proposal_ids = {
        proposal_id
        for proposal_id, path in data.proposal_paths.items()
        if path.parent == proposal_directory
    }
    preserved = {
        proposal["id"]: proposal
        for proposal in data.proposals
        if proposal.get("id") not in batch_proposal_ids
    }
    preserved.update(proposals_by_id)
    data.proposals = [preserved[key] for key in sorted(preserved)]
    data.proposal_paths = {
        proposal_id: path
        for proposal_id, path in data.proposal_paths.items()
        if proposal_id not in batch_proposal_ids
    }
    data.proposal_paths.update(proposal_paths_by_id)
    return data


def _affiliation_resolution_for_target(
    data: RepositoryData,
    proposal: dict[str, Any],
    resolution: dict[str, Any],
    *,
    organization_id: str,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a reviewer decision and bind it to the final organization ID."""

    identity = identity or _supported_affiliation_identity(data, proposal)
    if identity is None:
        raise SubmissionError(
            f"提案 {proposal.get('id')} 不是可安全处理的同邮箱任职冲突；"
            "请使用专门的身份纠错流程"
        )
    if not normalize_text(resolution.get("reason")):
        raise SubmissionError(f"提案 {proposal['id']} 的任职判定必须填写审核依据")
    action = resolution.get("action")
    make_primary = resolution.get("make_primary")
    former_affiliation_id = resolution.get("former_affiliation_id")
    if action not in {"append_current_affiliation", "transfer_current_affiliation"}:
        raise SubmissionError(f"提案 {proposal['id']} 的任职处理方式无效")
    if not isinstance(make_primary, bool):
        raise SubmissionError(f"提案 {proposal['id']} 的主任职设置无效")

    current_affiliations = identity["mentor"]["affiliations"]
    current_ids = {item["id"] for item in current_affiliations}
    current_organization_ids = {item["organization_id"] for item in current_affiliations}
    if organization_id in current_organization_ids:
        raise SubmissionError(
            f"提案 {proposal['id']} 的目标机构已是该导师的当前任职，"
            "无需新增双聘或调动"
        )
    if action == "append_current_affiliation":
        if former_affiliation_id is not None:
            raise SubmissionError(f"提案 {proposal['id']} 的新增双聘不能结束现有任职")
    else:
        if make_primary is not True:
            raise SubmissionError(f"提案 {proposal['id']} 的调动必须将新任职设为主任职")
        if former_affiliation_id not in current_ids:
            raise SubmissionError(
                f"提案 {proposal['id']} 选择的原当前任职不存在或已经不是当前任职"
            )
    return {
        "action": action,
        "organization_id": organization_id,
        "make_primary": make_primary,
        "former_affiliation_id": former_affiliation_id,
        "reason": normalize_text(resolution["reason"]),
    }


def _validate_affiliation_resolution_plan(
    planned: list[tuple[str, str, dict[str, Any]]],
) -> None:
    """Reject order-dependent affiliation outcomes within one batch review."""

    by_target: dict[tuple[str, str], set[tuple[str, bool, str | None]]] = {}
    primary_targets: dict[str, set[str]] = {}
    ended_affiliations: dict[tuple[str, str], set[str]] = {}
    for proposal_id, mentor_id, resolution in planned:
        organization_id = resolution["organization_id"]
        signature = (
            resolution["action"],
            resolution["make_primary"],
            resolution["former_affiliation_id"],
        )
        signatures = by_target.setdefault((mentor_id, organization_id), set())
        signatures.add(signature)
        if len(signatures) > 1:
            raise SubmissionError(
                f"导师 {mentor_id} 在机构 {organization_id} 的多条任职判定互相冲突；"
                f"请统一提案 {proposal_id} 的处理方式"
            )
        if resolution["make_primary"]:
            primary_targets.setdefault(mentor_id, set()).add(organization_id)
        former_affiliation_id = resolution.get("former_affiliation_id")
        if former_affiliation_id is not None:
            ended_affiliations.setdefault((mentor_id, former_affiliation_id), set()).add(
                organization_id
            )

    for mentor_id, organization_ids in primary_targets.items():
        if len(organization_ids) > 1:
            raise SubmissionError(
                f"导师 {mentor_id} 在同一批次中被设置了多个新主任职；请只保留一个"
            )
    for (mentor_id, affiliation_id), organization_ids in ended_affiliations.items():
        if len(organization_ids) > 1:
            raise SubmissionError(
                f"导师 {mentor_id} 的原任职 {affiliation_id} 被调动到多个机构；请只保留一个"
            )


def apply_organization_review(
    root: Path,
    review_comment: ReviewComment,
    review_pull: ReviewPull,
    *,
    schema_root: Path | None = None,
    allow_registry_drift: bool = False,
) -> AppliedOrganizationReview:
    root = root.resolve()
    trusted_schema_root = (schema_root or root).resolve()
    decision = review_comment.decision
    if review_pull.number != review_comment.pull_request_number:
        raise SubmissionError("评论与 Pull Request 元数据不一致")
    if decision["issue_number"] != review_pull.issue_number:
        raise SubmissionError("机构审核决策与批量投稿 Issue 不一致")

    manifest_path = root / "reviews" / "pending" / f"batch-issue-{review_pull.issue_number}.json"
    if not manifest_path.is_file():
        raise SubmissionError("Pull Request 中没有对应的机构审核清单")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    _validate_schema(
        trusted_schema_root,
        "organization-review.schema.json",
        manifest,
        "机构审核清单",
    )
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if decision["manifest_sha256"] != manifest_digest:
        raise SubmissionError("机构审核清单已经变化，请刷新审核页面后重新生成结果")
    if manifest["issue"]["number"] != review_pull.issue_number:
        raise SubmissionError("机构审核清单的 Issue 编号不一致")

    data = load_repository(root, validate=True, schema_root=trusted_schema_root)
    if (
        not allow_registry_drift
        and manifest["registry_sha256"] != _registry_digest(data.organizations_document)
    ):
        raise SubmissionError("机构注册表已经变化，请重新生成审核清单")
    resolution_path = (
        root / "reviews" / "resolutions" / f"batch-issue-{review_pull.issue_number}.json"
    )
    if resolution_path.exists():
        raise SubmissionError("该批次的机构审核已经应用")

    manifest_groups = {item["id"]: item for item in manifest["groups"]}
    if len(manifest_groups) != len(manifest["groups"]):
        raise SubmissionError("机构审核清单包含重复分组")
    manifest_rows = [row for group in manifest["groups"] for row in group["rows"]]
    manifest_proposal_ids = [row["proposal_id"] for row in manifest_rows]
    if len(manifest_proposal_ids) != len(set(manifest_proposal_ids)):
        raise SubmissionError("机构审核清单中的提案行重复")
    decision_groups = {item["group_id"]: item for item in decision["decisions"]}
    if len(decision_groups) != len(decision["decisions"]):
        raise SubmissionError("机构审核决策包含重复分组")
    if set(manifest_groups) != set(decision_groups):
        raise SubmissionError("机构审核决策必须完整覆盖清单中的全部分组")

    proposal_directory = root / manifest["proposal_directory"]
    proposals_by_id: dict[str, dict[str, Any]] = {}
    proposal_paths_by_id: dict[str, Path] = {}
    for path in sorted(proposal_directory.glob("*.json")) if proposal_directory.exists() else []:
        proposal = load_json(path)
        proposal_id = proposal.get("id")
        if isinstance(proposal_id, str):
            proposals_by_id[proposal_id] = proposal
            proposal_paths_by_id[proposal_id] = path
    expected_proposal_ids = set(manifest_proposal_ids)
    if set(proposals_by_id) != expected_proposal_ids:
        raise SubmissionError("机构审核清单与待审核提案集合不一致")
    for group in manifest["groups"]:
        for row in group["rows"]:
            proposal = proposals_by_id[row["proposal_id"]]
            submitted = proposal.get("submitted", {})
            submitted_path = {
                "university": submitted.get("submitted_university"),
                "school": submitted.get("submitted_school"),
                "department": submitted.get("submitted_department"),
            }
            expected_row = _manifest_row(
                data,
                proposal,
                pending_proposals=tuple(proposals_by_id.values()),
            )
            if "profile_url" not in row:
                expected_row.pop("profile_url", None)
            # Pending manifests produced before affiliation review existed remain
            # readable. They cannot opt into the new identity decision automatically.
            if "identity" not in row:
                expected_row.pop("identity", None)
            if row != expected_row or _group_id(submitted_path) != group["id"]:
                raise SubmissionError("机构审核清单与提案原始字段不一致，请重新生成清单")

    organizations_document = copy.deepcopy(data.organizations_document)
    organizations_by_id = {item["id"]: item for item in organizations_document["organizations"]}
    mapped_ids: set[str] = set()
    rejected_ids: set[str] = set()
    created_organization_ids: set[str] = set()
    updated_organization_ids: set[str] = set()
    pending_target_ids = {
        proposed_mentor_id(proposal)
        for proposal in proposals_by_id.values()
        if proposal.get("target_mentor_id") is None
    }
    decided_at = _iso_utc(review_comment.created_at)
    base_targets: dict[str, str | None] = {}
    overrides_by_group: dict[str, dict[str, dict[str, Any]]] = {}
    identity_resolutions_by_group: dict[str, dict[str, dict[str, Any]]] = {}
    _validate_independent_creation_references(decision, manifest_groups)

    # Resolve every institution first. A mentor can then be reassigned to an institution
    # created by another group in the same review, regardless of group sort order.
    for group_id in sorted(manifest_groups):
        group = manifest_groups[group_id]
        group_decision = decision_groups[group_id]
        overrides = {item["proposal_id"]: item for item in group_decision["row_overrides"]}
        group_row_ids = {item["proposal_id"] for item in group["rows"]}
        if len(overrides) != len(group_decision["row_overrides"]):
            raise SubmissionError(f"分组 {group_id} 包含重复逐行覆盖")
        if not set(overrides).issubset(group_row_ids):
            raise SubmissionError(f"分组 {group_id} 的逐行覆盖不属于该分组")
        overrides_by_group[group_id] = overrides
        identity_resolutions = {
            item["proposal_id"]: item
            for item in group_decision.get("identity_resolutions", [])
        }
        if len(identity_resolutions) != len(group_decision.get("identity_resolutions", [])):
            raise SubmissionError(f"分组 {group_id} 包含重复任职判定")
        if not set(identity_resolutions).issubset(group_row_ids):
            raise SubmissionError(f"分组 {group_id} 的任职判定不属于该分组")
        identity_resolutions_by_group[group_id] = identity_resolutions

        base_target: str | None = None
        if group_decision["action"] == "resolve":
            levels = group_decision["levels"]
            if levels:
                base_target, created, updated = _resolve_levels(
                    organizations_document,
                    organizations_by_id,
                    group["submitted"],
                    levels,
                    decided_at=decided_at,
                )
                created_organization_ids.update(created)
                updated_organization_ids.update(updated)
            elif not isinstance(group_decision.get("target_organization_id"), str):
                raise SubmissionError(f"分组 {group_id} 没有机构层级或独立最终机构")
        elif not normalize_text(group_decision.get("reason")):
            raise SubmissionError(f"拒绝分组 {group_id} 时必须填写原因")
        base_targets[group_id] = base_target

    created, updated = _apply_independent_organization_creations(
        organizations_document,
        organizations_by_id,
        decision.get("organization_creations", []),
        decided_at=decided_at,
    )
    created_organization_ids.update(created)
    updated_organization_ids.update(updated)

    final_targets: dict[str, str | None] = {}
    for group_id in sorted(manifest_groups):
        group_decision = decision_groups[group_id]
        if group_decision["action"] == "reject":
            if group_decision.get("target_organization_id") is not None:
                raise SubmissionError(f"被拒绝的分组 {group_id} 不能设置最终机构")
            if group_decision.get("save_path_correction") is True:
                raise SubmissionError(f"被拒绝的分组 {group_id} 不能保存路径纠错")
            final_targets[group_id] = None
            continue

        base_target = base_targets[group_id]
        requested_target = group_decision.get("target_organization_id")
        final_target = requested_target if isinstance(requested_target, str) else base_target
        organization = organizations_by_id.get(final_target)
        if organization is None or organization.get("status") != "active":
            raise SubmissionError(f"分组 {group_id} 的最终机构不存在或不可用：{final_target}")
        mapping_kind = group_decision.get("mapping_kind", "standard")
        mapping_reason = normalize_text(group_decision.get("mapping_reason"))
        save_path_correction = group_decision.get("save_path_correction", False)
        if mapping_kind == "standard":
            if final_target != base_target:
                raise SubmissionError(f"分组 {group_id} 的标准映射不能绕过已确认层级")
            if save_path_correction:
                raise SubmissionError(f"分组 {group_id} 的标准映射不需要保存路径纠错")
        else:
            if mapping_kind not in PATH_CORRECTION_KINDS:
                raise SubmissionError(f"分组 {group_id} 的路径纠错类型无效")
            if not isinstance(requested_target, str):
                raise SubmissionError(f"分组 {group_id} 的路径纠错必须明确最终机构")
            if not mapping_reason:
                raise SubmissionError(f"分组 {group_id} 的路径纠错必须填写审核依据")
            expected_target_type = {
                "department_as_school": "school",
                "department_as_institute": "institute",
            }.get(mapping_kind)
            if (
                expected_target_type is not None
                and organization.get("type") != expected_target_type
            ):
                raise SubmissionError(
                    f"分组 {group_id} 的路径纠错类型与最终机构不一致"
                )
        final_targets[group_id] = final_target

    pending_target_organization_ids: dict[str, str] = {}
    for group_id, group in manifest_groups.items():
        group_decision = decision_groups[group_id]
        overrides = overrides_by_group[group_id]
        for row in group["rows"]:
            proposal = proposals_by_id[row["proposal_id"]]
            if proposal.get("target_mentor_id") is not None:
                continue
            override = overrides.get(row["proposal_id"])
            if override is not None and override["action"] == "reject":
                continue
            if override is not None:
                target_id = override.get("organization_id")
            elif group_decision["action"] == "reject":
                continue
            else:
                target_id = final_targets[group_id]
            if isinstance(target_id, str):
                pending_target_organization_ids[proposed_mentor_id(proposal)] = target_id

    for group_id in sorted(manifest_groups):
        group = manifest_groups[group_id]
        group_decision = decision_groups[group_id]
        overrides = overrides_by_group[group_id]
        identity_resolutions = identity_resolutions_by_group[group_id]
        final_target = final_targets[group_id]
        for row in group["rows"]:
            proposal_id = row["proposal_id"]
            override = overrides.get(proposal_id)
            identity_resolution = identity_resolutions.get(proposal_id)
            if override is not None and override["action"] == "reject":
                if identity_resolution is not None:
                    raise SubmissionError(f"被拒绝的提案 {proposal_id} 不能同时新增或调动任职")
                if not normalize_text(override.get("reason")):
                    raise SubmissionError(f"拒绝提案 {proposal_id} 时必须填写原因")
                rejected_ids.add(proposal_id)
                continue
            if override is not None:
                target_id = override.get("organization_id")
                organization = organizations_by_id.get(target_id)
                if organization is not None and organization.get("status") != "active":
                    organization = None
                if organization is None:
                    raise SubmissionError(f"逐行覆盖机构不存在：{target_id}")
            elif group_decision["action"] == "reject":
                if identity_resolution is not None:
                    raise SubmissionError(f"被拒绝的提案 {proposal_id} 不能同时新增或调动任职")
                rejected_ids.add(proposal_id)
                continue
            else:
                target_id = final_target
            if not isinstance(target_id, str):
                raise SubmissionError(f"提案 {proposal_id} 没有最终机构")
            proposal = proposals_by_id[proposal_id]
            if proposal.get("affiliation_resolution") is not None:
                raise SubmissionError(f"提案 {proposal_id} 已包含任职判定，不能重复应用机构审核")
            proposal["accepted"]["organization_id"] = target_id
            proposal["review_reasons"] = [
                reason
                for reason in proposal["review_reasons"]
                if reason not in ORGANIZATION_REVIEW_REASONS
            ]
            identity = row.get("identity")
            if identity is None:
                if identity_resolution is not None:
                    raise SubmissionError(f"提案 {proposal_id} 不支持当前任职判定")
            else:
                supported_identity = (
                    identity
                    if identity.get("target_mentor_id") in pending_target_ids
                    else _supported_affiliation_identity(
                        data,
                        proposal,
                        pending_proposals=tuple(proposals_by_id.values()),
                    )
                )
                if supported_identity != identity:
                    raise SubmissionError("导师身份或当前任职已经变化，请刷新审核清单")
                current_organization_ids = {
                    item["organization_id"] for item in identity["mentor"]["affiliations"]
                }
                pending_organization_id = pending_target_organization_ids.get(
                    identity.get("target_mentor_id")
                )
                if pending_organization_id is not None:
                    current_organization_ids.discard(None)
                    current_organization_ids.add(pending_organization_id)
                if target_id in current_organization_ids:
                    if identity_resolution is not None:
                        raise SubmissionError(
                            f"提案 {proposal_id} 的目标机构已是导师当前任职，无需新增双聘或调动"
                        )
                    proposal["review_reasons"] = [
                        reason
                        for reason in proposal["review_reasons"]
                        if reason not in AFFILIATION_IDENTITY_REASONS
                    ]
                else:
                    if identity_resolution is None:
                        raise SubmissionError(
                            f"提案 {proposal_id} 疑似同一导师但学院不一致；"
                            "请选择新增双聘任职、已调动到新学院，或拒绝该导师"
                        )
                    affiliation_resolution = _affiliation_resolution_for_target(
                        data,
                        proposal,
                        identity_resolution,
                        organization_id=target_id,
                        identity=identity,
                    )
                    proposal["affiliation_resolution"] = affiliation_resolution
                    proposal["review_reasons"] = [
                        reason
                        for reason in proposal["review_reasons"]
                        if reason not in AFFILIATION_IDENTITY_REASONS
                    ]
            proposal["auto_eligible"] = False
            mapped_ids.add(proposal_id)

    for rejected_proposal_id in sorted(rejected_ids):
        rejected_proposal = proposals_by_id[rejected_proposal_id]
        if rejected_proposal.get("target_mentor_id") is not None:
            continue
        rejected_mentor_id = proposed_mentor_id(rejected_proposal)
        dependents = [
            proposal
            for proposal_id, proposal in proposals_by_id.items()
            if proposal_id not in rejected_ids
            and proposal.get("target_mentor_id") == rejected_mentor_id
        ]
        if not dependents:
            continue
        replacement = min(
            dependents,
            key=lambda item: (item["issue"].get("batch_row", 0), item["id"]),
        )
        replacement_mentor_id = proposed_mentor_id(replacement)
        replacement_organization_id = replacement["accepted"]["organization_id"]
        rejected_affiliation_id = stable_proposal_entity_id(
            "aff", rejected_proposal, "affiliation"
        )
        replacement_affiliation_id = stable_proposal_entity_id(
            "aff", replacement, "affiliation"
        )
        replacement["target_mentor_id"] = None
        replacement["match_status"] = "new"
        replacement.pop("affiliation_resolution", None)
        replacement["review_reasons"] = [
            reason
            for reason in replacement["review_reasons"]
            if reason not in AFFILIATION_IDENTITY_REASONS
        ]
        for dependent in dependents:
            if dependent is replacement:
                continue
            dependent["target_mentor_id"] = replacement_mentor_id
            if dependent["accepted"]["organization_id"] == replacement_organization_id:
                dependent.pop("affiliation_resolution", None)
                continue
            resolution = dependent.get("affiliation_resolution")
            if resolution is None:
                raise SubmissionError(
                    f"提案 {dependent['id']} 的基准导师行已被拒绝，"
                    "保留后的任职关系需要重新审核"
                )
            if resolution.get("former_affiliation_id") == rejected_affiliation_id:
                resolution["former_affiliation_id"] = replacement_affiliation_id

    planned_affiliation_resolutions = [
        (proposal_id, proposal["target_mentor_id"], proposal["affiliation_resolution"])
        for proposal_id, proposal in proposals_by_id.items()
        if proposal_id not in rejected_ids
        and proposal.get("target_mentor_id") is not None
        and proposal.get("affiliation_resolution") is not None
    ]
    _validate_affiliation_resolution_plan(planned_affiliation_resolutions)

    path_corrections: list[dict[str, Any]] = []
    for group_id in sorted(manifest_groups):
        group = manifest_groups[group_id]
        group_decision = decision_groups[group_id]
        mapping_kind = group_decision.get("mapping_kind", "standard")
        if (
            group_decision["action"] != "resolve"
            or mapping_kind == "standard"
            or group_decision.get("save_path_correction") is not True
        ):
            continue
        target_id = final_targets[group_id]
        if not isinstance(target_id, str):
            continue
        overrides = overrides_by_group[group_id]
        corrected_rows = [
            row
            for row in group["rows"]
            if row["proposal_id"] not in rejected_ids
            and (
                row["proposal_id"] not in overrides
                or overrides[row["proposal_id"]].get("organization_id") == target_id
            )
        ]
        proposal_ids = [row["proposal_id"] for row in corrected_rows]
        if not proposal_ids:
            continue
        source_domains = {
            hostname_for_url(source_url)
            for row in corrected_rows
            for source_url in (row.get("source_url"), row.get("profile_url"))
            if isinstance(source_url, str) and source_url
        }
        path_corrections.append(
            {
                "submitted": copy.deepcopy(group["submitted"]),
                "target_organization_id": target_id,
                "kind": mapping_kind,
                "reason": normalize_text(group_decision["mapping_reason"]),
                "proposal_ids": sorted(proposal_ids),
                "source_domains": sorted(source_domains),
            }
        )

    for proposal_id in rejected_ids:
        proposals_by_id.pop(proposal_id)
        proposal_paths_by_id.pop(proposal_id)

    registry = OrganizationRegistry(organizations_document["organizations"])
    for proposal_id in mapped_ids:
        proposal = proposals_by_id[proposal_id]
        target_id = proposal["accepted"]["organization_id"]
        if target_id not in registry.by_id:
            raise SubmissionError(f"审核后的机构不存在：{target_id}")
        if not registry.url_is_approved(proposal["accepted"]["source_url"], target_id):
            raise SubmissionError(f"提案 {proposal_id} 的官方来源不属于所选机构批准域名")
        profile_url = proposal["accepted"].get("profile_url")
        if profile_url and not registry.url_is_approved(profile_url, target_id):
            raise SubmissionError(f"提案 {proposal_id} 的导师详情页不属于所选机构批准域名")

    candidate_data = _candidate_repository_data(
        data,
        organizations_document,
        proposals_by_id,
        proposal_paths_by_id,
        proposal_directory,
    )
    validate_repository_data(candidate_data, schema_root=trusted_schema_root)

    resolution = {
        "schema_version": 1,
        "id": f"organization_review_issue_{review_pull.issue_number}",
        "issue": copy.deepcopy(manifest["issue"]),
        "pull_request_number": review_pull.number,
        "review_comment_id": review_comment.comment_id,
        "reviewer": {
            "github_user_id": review_comment.reviewer_id,
            "github_login": review_comment.reviewer_login,
            "author_association": review_comment.author_association,
        },
        "manifest_sha256": manifest_digest,
        "decided_at": decided_at,
        "created_organization_ids": sorted(created_organization_ids),
        "updated_organization_ids": sorted(updated_organization_ids),
        "mapped_proposal_ids": sorted(mapped_ids),
        "rejected_proposal_ids": sorted(rejected_ids),
        "invalid_rows": sorted(item["batch_row"] for item in manifest["invalid_rows"]),
        "organization_creations": copy.deepcopy(
            decision.get("organization_creations", [])
        ),
        "path_corrections": path_corrections,
        "decisions": copy.deepcopy(decision["decisions"]),
    }
    _validate_schema(
        trusted_schema_root,
        "organization-review-resolution.schema.json",
        resolution,
        "机构审核记录",
    )

    write_yaml_atomic(root / "registry" / "organizations.yml", organizations_document)
    for proposal_id in mapped_ids:
        write_json_atomic(proposal_paths_by_id[proposal_id], proposals_by_id[proposal_id])
    for proposal_id in rejected_ids:
        path = data.proposal_paths.get(proposal_id)
        if path is not None:
            path.unlink(missing_ok=True)
    write_json_atomic(resolution_path, resolution)
    load_repository(root, validate=True, schema_root=trusted_schema_root)

    remaining_paths = [proposal_paths_by_id[key] for key in sorted(proposal_paths_by_id)]
    ready_for_finalization = False
    finalization_error: str | None = None
    if remaining_paths:
        try:
            check_proposal_set(root, remaining_paths, schema_root=trusted_schema_root)
            ready_for_finalization = True
        except (RepositoryValidationError, SubmissionError, OSError, ValueError) as error:
            finalization_error = str(error).splitlines()[0][:500]

    return AppliedOrganizationReview(
        remaining_proposals=len(remaining_paths),
        mapped_proposals=len(mapped_ids),
        rejected_proposals=len(rejected_ids),
        created_organizations=len(created_organization_ids),
        updated_organizations=len(updated_organization_ids),
        invalid_rows=len(manifest["invalid_rows"]),
        ready_for_finalization=ready_for_finalization,
        finalization_error=finalization_error,
    )
