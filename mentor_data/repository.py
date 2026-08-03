from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from .errors import RepositoryValidationError
from .identifiers import proposed_mentor_id
from .io_utils import iter_json_files, load_json, load_yaml
from .normalization import (
    host_matches_domain,
    is_generic_email,
    is_valid_email,
    normalize_email,
    normalize_organization_key,
)
from .organizations import OrganizationRegistry


@dataclass(slots=True)
class RepositoryData:
    root: Path
    policy: dict[str, Any]
    blocked: dict[str, Any]
    organizations_document: dict[str, Any]
    registry: OrganizationRegistry
    mentors: list[dict[str, Any]]
    mentor_paths: dict[str, Path]
    claims: list[dict[str, Any]]
    claim_paths: dict[str, Path]
    resolutions: list[dict[str, Any]]
    organization_review_resolutions: list[dict[str, Any]]
    organization_review_resolution_paths: dict[str, Path]
    proposals: list[dict[str, Any]]
    proposal_paths: dict[str, Path]
    report_proposals: list[dict[str, Any]]
    report_proposal_paths: dict[str, Path]
    revocations: dict[str, Any]


def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    messages: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        messages.append(f"{location}: {error.message}")
    return messages


def _load_json_collection(path: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    values: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}
    for candidate in iter_json_files(path):
        value = load_json(candidate)
        values.append(value)
        value_id = value.get("id") if isinstance(value, dict) else None
        if isinstance(value_id, str):
            paths[value_id] = candidate
    return values, paths


def load_repository(root: Path, *, validate: bool = True) -> RepositoryData:
    resolved_root = root.resolve()
    organizations_document = load_yaml(resolved_root / "registry" / "organizations.yml")
    mentors, mentor_paths = _load_json_collection(resolved_root / "records" / "mentors")
    claims, claim_paths = _load_json_collection(resolved_root / "claims")
    resolutions, _ = _load_json_collection(resolved_root / "reports" / "resolutions")
    organization_review_resolutions, organization_review_resolution_paths = (
        _load_json_collection(resolved_root / "reviews" / "resolutions")
    )
    proposals, proposal_paths = _load_json_collection(resolved_root / "proposals")
    report_proposals, report_proposal_paths = _load_json_collection(
        resolved_root / "reports" / "pending"
    )
    data = RepositoryData(
        root=resolved_root,
        policy=load_yaml(resolved_root / "registry" / "policy.yml"),
        blocked=load_yaml(resolved_root / "registry" / "blocked-contributors.yml"),
        organizations_document=organizations_document,
        registry=OrganizationRegistry(organizations_document.get("organizations", [])),
        mentors=mentors,
        mentor_paths=mentor_paths,
        claims=claims,
        claim_paths=claim_paths,
        resolutions=resolutions,
        organization_review_resolutions=organization_review_resolutions,
        organization_review_resolution_paths=organization_review_resolution_paths,
        proposals=proposals,
        proposal_paths=proposal_paths,
        report_proposals=report_proposals,
        report_proposal_paths=report_proposal_paths,
        revocations=load_yaml(resolved_root / "registry" / "revocations.yml"),
    )
    if validate:
        validate_repository_data(data)
    return data


def validate_repository(root: Path) -> RepositoryData:
    return load_repository(root, validate=True)


def validate_repository_data(data: RepositoryData) -> None:
    issues: list[str] = []
    schemas = {
        "organizations": load_json(data.root / "schemas" / "organization.schema.json"),
        "mentor": load_json(data.root / "schemas" / "mentor.schema.json"),
        "claim": load_json(data.root / "schemas" / "claim.schema.json"),
        "resolution": load_json(data.root / "schemas" / "resolution.schema.json"),
        "correction_patch": load_json(data.root / "schemas" / "correction-patch.schema.json"),
        "proposal": load_json(data.root / "schemas" / "proposal.schema.json"),
        "report_proposal": load_json(data.root / "schemas" / "report-proposal.schema.json"),
        "organization_review_resolution": load_json(
            data.root / "schemas" / "organization-review-resolution.schema.json"
        ),
    }

    for message in _schema_errors(schemas["organizations"], data.organizations_document):
        issues.append(f"registry/organizations.yml: {message}")
    for mentor in data.mentors:
        mentor_id = mentor.get("id", "<unknown>")
        for message in _schema_errors(schemas["mentor"], mentor):
            issues.append(f"mentor {mentor_id}: {message}")
    for claim in data.claims:
        claim_id = claim.get("id", "<unknown>")
        for message in _schema_errors(schemas["claim"], claim):
            issues.append(f"claim {claim_id}: {message}")
    for resolution in data.resolutions:
        resolution_id = resolution.get("id", "<unknown>")
        for message in _schema_errors(schemas["resolution"], resolution):
            issues.append(f"resolution {resolution_id}: {message}")
        accepted = resolution.get("accepted")
        if isinstance(accepted, dict) and accepted:
            for message in _schema_errors(schemas["correction_patch"], accepted):
                issues.append(f"resolution {resolution_id} accepted: {message}")
    for proposal in data.proposals:
        proposal_id = proposal.get("id", "<unknown>")
        for message in _schema_errors(schemas["proposal"], proposal):
            issues.append(f"proposal {proposal_id}: {message}")
    for proposal in data.report_proposals:
        proposal_id = proposal.get("id", "<unknown>")
        for message in _schema_errors(schemas["report_proposal"], proposal):
            issues.append(f"report proposal {proposal_id}: {message}")
        accepted = proposal.get("accepted")
        if isinstance(accepted, dict) and accepted:
            for message in _schema_errors(schemas["correction_patch"], accepted):
                issues.append(f"report proposal {proposal_id} accepted: {message}")
    for resolution in data.organization_review_resolutions:
        resolution_id = resolution.get("id", "<unknown>")
        for message in _schema_errors(schemas["organization_review_resolution"], resolution):
            issues.append(f"organization review {resolution_id}: {message}")

    _validate_policy(data, issues)
    _validate_organizations(data, issues)
    _validate_claims_and_blocking(data, issues)
    _validate_mentors(data, issues)
    _validate_resolutions(data, issues)
    _validate_proposals(data, issues)
    _validate_report_proposals(data, issues)
    _validate_organization_review_resolutions(data, issues)
    _validate_revocations(data, issues)

    if issues:
        raise RepositoryValidationError(sorted(set(issues)))


def _validate_policy(data: RepositoryData, issues: list[str]) -> None:
    policy = data.policy
    if policy.get("schema_version") != 1:
        issues.append("registry/policy.yml: schema_version 必须为 1")
    age = policy.get("minimum_auto_merge_account_age_days")
    if not isinstance(age, int) or age < 0:
        issues.append("registry/policy.yml: minimum_auto_merge_account_age_days 必须是非负整数")
    data_license = policy.get("data_license")
    if not isinstance(data_license, dict):
        issues.append("registry/policy.yml: data_license 必须存在")
    else:
        if data_license.get("spdx_id") != "CC-BY-4.0":
            issues.append("registry/policy.yml: data_license.spdx_id 必须为 CC-BY-4.0")
        if data_license.get("url") != "https://creativecommons.org/licenses/by/4.0/":
            issues.append("registry/policy.yml: data_license.url 必须指向 CC BY 4.0 官方页面")
        if not isinstance(data_license.get("name"), str) or not data_license["name"].strip():
            issues.append("registry/policy.yml: data_license.name 必须是非空字符串")
        if (
            not isinstance(data_license.get("attribution"), str)
            or not data_license["attribution"].strip()
        ):
            issues.append("registry/policy.yml: data_license.attribution 必须是非空字符串")
    limits = policy.get("limits")
    if not isinstance(limits, dict):
        issues.append("registry/policy.yml: limits 必须存在")
    else:
        for key, value in limits.items():
            if not isinstance(value, int) or value <= 0:
                issues.append(f"registry/policy.yml: limits.{key} 必须是正整数")


def _validate_organizations(data: RepositoryData, issues: list[str]) -> None:
    organizations = data.registry.organizations
    ids = [item.get("id") for item in organizations]
    if len(ids) != len(set(ids)):
        issues.append("registry/organizations.yml: organization id 重复")

    alias_index: dict[tuple[str | None, str], str] = {}
    for organization in organizations:
        organization_id = organization.get("id")
        parent_id = organization.get("parent_id")
        organization_type = organization.get("type")
        if parent_id is not None and parent_id not in data.registry.by_id:
            issues.append(f"organization {organization_id}: parent_id {parent_id} 不存在")
        if organization_type == "university" and parent_id is not None:
            issues.append(f"organization {organization_id}: university 的 parent_id 必须为空")
        if organization_type != "university" and parent_id is None:
            issues.append(f"organization {organization_id}: 非 university 必须有 parent_id")
        if organization.get("status") in {"merged", "closed"} and not organization.get(
            "successor_id"
        ):
            issues.append(f"organization {organization_id}: merged/closed 必须填写 successor_id")
        successor_id = organization.get("successor_id")
        if successor_id is not None and successor_id not in data.registry.by_id:
            issues.append(f"organization {organization_id}: successor_id {successor_id} 不存在")

        for name in [organization.get("canonical_name", ""), *organization.get("aliases", [])]:
            key = (parent_id, normalize_organization_key(name))
            if not key[1]:
                continue
            existing = alias_index.get(key)
            if existing is not None and existing != organization_id:
                issues.append(
                    f"organization {organization_id}: 名称/别名 {name!r} 与同级机构 {existing} 冲突"
                )
            alias_index[key] = organization_id

        seen: set[str] = set()
        current = organization
        while current.get("parent_id") is not None:
            current_id = current["id"]
            if current_id in seen:
                issues.append(f"organization {organization_id}: 机构父级形成循环")
                break
            seen.add(current_id)
            parent = data.registry.by_id.get(current["parent_id"])
            if parent is None:
                break
            current = parent

        approved_domains = data.registry.approved_domains(organization_id)
        for url in organization.get("official_urls", []):
            hostname = (urlsplit(url).hostname or "").lower()
            if not any(host_matches_domain(hostname, domain) for domain in approved_domains):
                issues.append(
                    f"organization {organization_id}: 官方 URL {url} 不属于本机构或上级批准域名"
                )


def _blocked_scopes(data: RepositoryData) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    if data.blocked.get("schema_version") != 1:
        return result
    for item in data.blocked.get("blocked", []):
        user_id = item.get("github_user_id")
        scopes = item.get("scopes", [])
        if isinstance(user_id, int) and user_id > 0 and isinstance(scopes, list):
            result.setdefault(user_id, set()).update(str(scope) for scope in scopes)
    return result


def _validate_claims_and_blocking(data: RepositoryData, issues: list[str]) -> None:
    claim_ids = [item.get("id") for item in data.claims]
    if len(claim_ids) != len(set(claim_ids)):
        issues.append("claims: claim id 重复")
    blocked = _blocked_scopes(data)
    mentor_ids = {item.get("id") for item in data.mentors}
    for claim in data.claims:
        claim_id = claim.get("id")
        contributor = claim.get("contributor", {})
        user_id = contributor.get("github_user_id")
        if claim.get("mentor_id") not in mentor_ids:
            issues.append(f"claim {claim_id}: mentor_id 不存在")
        is_active = claim.get("status") in {"accepted", "partially_accepted"}
        if is_active and "contribute" in blocked.get(user_id, set()):
            issues.append(f"claim {claim_id}: 封禁用户 {user_id} 仍有有效贡献")
        claim_path = data.claim_paths.get(claim_id)
        if claim_path is not None and isinstance(user_id, int):
            relative_parts = claim_path.relative_to(data.root / "claims").parts
            if len(relative_parts) < 2 or relative_parts[0] != str(user_id):
                issues.append(f"claim {claim_id}: 文件目录必须使用贡献者数字 ID {user_id}")
        accepted = claim.get("accepted", {})
        organization_id = accepted.get("organization_id")
        source_url = accepted.get("source_url")
        if organization_id not in data.registry.by_id:
            issues.append(f"claim {claim_id}: accepted.organization_id 不存在")
        elif isinstance(source_url, str) and not data.registry.url_is_approved(
            source_url, organization_id
        ):
            issues.append(f"claim {claim_id}: source_url 不属于批准机构域名")


def _validate_mentors(data: RepositoryData, issues: list[str]) -> None:
    claim_by_id = {item.get("id"): item for item in data.claims}
    resolution_by_id = {item.get("id"): item for item in data.resolutions}
    mentor_ids = [item.get("id") for item in data.mentors]
    if len(mentor_ids) != len(set(mentor_ids)):
        issues.append("records/mentors: mentor id 重复")

    email_owners: dict[str, tuple[str, str]] = {}
    profile_owners: dict[str, str] = {}
    for mentor in data.mentors:
        mentor_id = mentor.get("id")
        status = mentor.get("status")
        names = mentor.get("names", [])
        if sum(1 for item in names if item.get("is_primary")) != 1:
            issues.append(f"mentor {mentor_id}: 必须且只能有一个主要姓名")

        claim_ids = mentor.get("claim_ids", [])
        if status != "removed" and not claim_ids:
            issues.append(f"mentor {mentor_id}: 非 removed 导师必须至少有一个 Claim")
        for claim_id in claim_ids:
            claim = claim_by_id.get(claim_id)
            if claim is None:
                issues.append(f"mentor {mentor_id}: claim_id {claim_id} 不存在")
            elif claim.get("mentor_id") != mentor_id:
                issues.append(f"mentor {mentor_id}: claim {claim_id} 指向其他导师")
            elif claim.get("status") not in {"accepted", "partially_accepted"}:
                issues.append(f"mentor {mentor_id}: 当前来源 claim {claim_id} 不是有效状态")
        resolution_ids = mentor.get("resolution_ids", [])
        for resolution_id in resolution_ids:
            resolution = resolution_by_id.get(resolution_id)
            if resolution is None:
                issues.append(f"mentor {mentor_id}: resolution_id {resolution_id} 不存在")
            elif resolution.get("mentor_id") != mentor_id:
                issues.append(f"mentor {mentor_id}: resolution {resolution_id} 指向其他导师")

        affiliations = mentor.get("affiliations", [])
        affiliation_by_id = {item.get("id"): item for item in affiliations}
        if len(affiliation_by_id) != len(affiliations):
            issues.append(f"mentor {mentor_id}: affiliation id 重复")
        current_affiliations = [item for item in affiliations if item.get("status") == "current"]
        primary_affiliations = [item for item in current_affiliations if item.get("is_primary")]
        if status == "active" and len(primary_affiliations) != 1:
            issues.append(f"mentor {mentor_id}: active 导师必须且只能有一个主要当前任职")
        if len(primary_affiliations) > 1:
            issues.append(f"mentor {mentor_id}: 主要当前任职不能超过一个")
        if status in {"retired", "departed", "deceased", "removed"} and current_affiliations:
            issues.append(f"mentor {mentor_id}: {status} 导师不能保留当前任职")
        for affiliation in affiliations:
            organization_id = affiliation.get("organization_id")
            if organization_id not in data.registry.by_id:
                issues.append(f"mentor {mentor_id}: 任职机构 {organization_id} 不存在")
            source_url = affiliation.get("source_url")
            if (
                isinstance(source_url, str)
                and organization_id in data.registry.by_id
                and not data.registry.url_is_approved(source_url, organization_id)
            ):
                issues.append(f"mentor {mentor_id}: 任职来源 URL 不属于批准域名")
            _check_nested_sources(
                mentor_id,
                affiliation,
                claim_by_id,
                resolution_by_id,
                issues,
            )

        contacts = mentor.get("contacts", [])
        current_contacts = [item for item in contacts if item.get("status") == "current"]
        primary_contacts = [item for item in current_contacts if item.get("is_primary")]
        if status == "active" and len(primary_contacts) != 1:
            issues.append(f"mentor {mentor_id}: active 导师必须且只能有一个主要当前邮箱")
        if len(primary_contacts) > 1:
            issues.append(f"mentor {mentor_id}: 主要当前邮箱不能超过一个")
        if status in {"retired", "departed", "deceased", "removed"} and current_contacts:
            issues.append(f"mentor {mentor_id}: {status} 导师不能保留当前邮箱")
        for contact in contacts:
            value = contact.get("value", "")
            normalized = normalize_email(value)
            if contact.get("normalized_value") != normalized:
                issues.append(f"mentor {mentor_id}: 邮箱 {value} 的 normalized_value 不正确")
            if not is_valid_email(normalized):
                issues.append(f"mentor {mentor_id}: 邮箱 {value} 格式无效")
            if contact.get("status") == "generic" and not is_generic_email(normalized):
                issues.append(f"mentor {mentor_id}: 邮箱 {value} 标记 generic 但不在通用邮箱规则中")
            if is_generic_email(normalized) and contact.get("status") not in {"generic", "shared"}:
                issues.append(f"mentor {mentor_id}: 通用邮箱 {value} 必须标记 generic/shared")
            if contact.get("is_primary") and contact.get("status") != "current":
                issues.append(f"mentor {mentor_id}: 非 current 邮箱不能是主要邮箱")
            if contact.get("is_primary") and contact.get("status") in {"generic", "shared"}:
                issues.append(f"mentor {mentor_id}: 通用或共享邮箱不能是主要邮箱")
            affiliation_id = contact.get("affiliation_id")
            if affiliation_id is not None and affiliation_id not in affiliation_by_id:
                issues.append(f"mentor {mentor_id}: 邮箱关联任职 {affiliation_id} 不存在")
            contact_org_ids = _item_organization_ids(affiliation_id, affiliations)
            source_url = contact.get("source_url")
            if (
                isinstance(source_url, str)
                and contact_org_ids
                and not any(
                    data.registry.url_is_approved(source_url, organization_id)
                    for organization_id in contact_org_ids
                )
            ):
                issues.append(f"mentor {mentor_id}: 邮箱来源 URL 不属于关联任职批准域名")
            if contact.get("status") in {"current", "former"}:
                existing = email_owners.get(normalized)
                if existing is not None and existing[0] != mentor_id:
                    issues.append(
                        f"mentor {mentor_id}: 邮箱 {normalized} 已由导师 "
                        f"{existing[0]} 以 {existing[1]} 状态占用"
                    )
                email_owners[normalized] = (mentor_id, contact.get("status"))
            _check_nested_sources(
                mentor_id,
                contact,
                claim_by_id,
                resolution_by_id,
                issues,
            )

        for profile in mentor.get("profiles", []):
            url = profile.get("url")
            if profile.get("status") == "current" and isinstance(url, str):
                existing = profile_owners.get(url)
                if existing is not None and existing != mentor_id:
                    issues.append(f"mentor {mentor_id}: 当前主页 {url} 已属于导师 {existing}")
                profile_owners[url] = mentor_id
            affiliation_id = profile.get("affiliation_id")
            if affiliation_id is not None and affiliation_id not in affiliation_by_id:
                issues.append(f"mentor {mentor_id}: 主页关联任职 {affiliation_id} 不存在")
            profile_org_ids = _item_organization_ids(affiliation_id, affiliations)
            if (
                isinstance(url, str)
                and profile_org_ids
                and not any(
                    data.registry.url_is_approved(url, organization_id)
                    for organization_id in profile_org_ids
                )
            ):
                issues.append(f"mentor {mentor_id}: 主页 URL 不属于关联任职批准域名")
            _check_nested_sources(
                mentor_id,
                profile,
                claim_by_id,
                resolution_by_id,
                issues,
            )

        for name in names:
            _check_nested_sources(
                mentor_id,
                name,
                claim_by_id,
                resolution_by_id,
                issues,
            )

        status_source_url = mentor.get("status_source_url")
        organization_ids = {
            item.get("organization_id")
            for item in affiliations
            if item.get("organization_id") in data.registry.by_id
        }
        if (
            isinstance(status_source_url, str)
            and organization_ids
            and not any(
                data.registry.url_is_approved(status_source_url, organization_id)
                for organization_id in organization_ids
            )
        ):
            issues.append(f"mentor {mentor_id}: 状态来源 URL 不属于任职机构批准域名")

        for field, provenance in mentor.get("field_provenance", {}).items():
            for source_id in provenance:
                if source_id not in claim_ids and source_id not in resolution_ids:
                    issues.append(
                        f"mentor {mentor_id}: 字段 {field} 的来源 {source_id} "
                        "不在当前 claim_ids/resolution_ids 中"
                    )


def _item_organization_ids(
    affiliation_id: str | None,
    affiliations: list[dict[str, Any]],
) -> set[str]:
    if affiliation_id is not None:
        return {
            item["organization_id"]
            for item in affiliations
            if item.get("id") == affiliation_id and isinstance(item.get("organization_id"), str)
        }
    return {
        item["organization_id"]
        for item in affiliations
        if isinstance(item.get("organization_id"), str)
    }


def _check_nested_sources(
    mentor_id: str,
    value: dict[str, Any],
    claim_by_id: dict[str, dict[str, Any]],
    resolution_by_id: dict[str, dict[str, Any]],
    issues: list[str],
) -> None:
    claim_ids = value.get("claim_ids", [])
    resolution_ids = value.get("resolution_ids", [])
    if not claim_ids and not resolution_ids:
        issues.append(f"mentor {mentor_id}: 嵌套字段缺少 Claim 或 Resolution 来源")
    for claim_id in claim_ids:
        claim = claim_by_id.get(claim_id)
        if claim is None:
            issues.append(f"mentor {mentor_id}: 嵌套来源 {claim_id} 不存在")
        elif claim.get("mentor_id") != mentor_id:
            issues.append(f"mentor {mentor_id}: 嵌套来源 {claim_id} 指向其他导师")
    for resolution_id in resolution_ids:
        resolution = resolution_by_id.get(resolution_id)
        if resolution is None:
            issues.append(f"mentor {mentor_id}: 嵌套来源 {resolution_id} 不存在")
        elif resolution.get("mentor_id") != mentor_id:
            issues.append(f"mentor {mentor_id}: 嵌套来源 {resolution_id} 指向其他导师")


def _validate_resolutions(data: RepositoryData, issues: list[str]) -> None:
    mentor_ids = {item.get("id") for item in data.mentors}
    removed_mentor_ids: set[str] = set()
    for event in data.revocations.get("revocations", []):
        removed_mentor_ids.update(str(item) for item in event.get("removed_mentor_ids", []))
        if event.get("status") == "removed" and event.get("community_record_id"):
            removed_mentor_ids.add(str(event["community_record_id"]))
    blocked = _blocked_scopes(data)
    for resolution in data.resolutions:
        resolution_id = resolution.get("id")
        if (
            resolution.get("mentor_id") not in mentor_ids
            and resolution.get("mentor_id") not in removed_mentor_ids
        ):
            issues.append(f"resolution {resolution_id}: mentor_id 不存在")
        reporter_id = resolution.get("reporter", {}).get("github_user_id")
        if "report" in blocked.get(reporter_id, set()) and resolution.get("decision") not in {
            "rejected",
            "duplicate",
        }:
            issues.append(f"resolution {resolution_id}: 封禁反馈者不能产生有效修改")


def _validate_proposals(data: RepositoryData, issues: list[str]) -> None:
    ids = [item.get("id") for item in data.proposals]
    if len(ids) != len(set(ids)):
        issues.append("proposals: proposal id 重复")
    blocked = _blocked_scopes(data)
    mentor_ids = {item.get("id") for item in data.mentors}
    pending_mentor_ids = {
        proposed_mentor_id(item)
        for item in data.proposals
        if item.get("target_mentor_id") is None
        and isinstance(item.get("contributor"), dict)
        and isinstance(item.get("issue"), dict)
        and isinstance(item.get("id"), str)
    }
    for proposal in data.proposals:
        proposal_id = proposal.get("id")
        user_id = proposal.get("contributor", {}).get("github_user_id")
        if "contribute" in blocked.get(user_id, set()):
            issues.append(f"proposal {proposal_id}: 封禁用户仍有待审核投稿")
        target_mentor_id = proposal.get("target_mentor_id")
        if (
            target_mentor_id is not None
            and target_mentor_id not in mentor_ids
            and target_mentor_id not in pending_mentor_ids
        ):
            issues.append(f"proposal {proposal_id}: target_mentor_id 不存在")
        organization_id = proposal.get("accepted", {}).get("organization_id")
        source_url = proposal.get("accepted", {}).get("source_url")
        if organization_id is not None:
            if organization_id not in data.registry.by_id:
                issues.append(f"proposal {proposal_id}: accepted.organization_id 不存在")
            elif isinstance(source_url, str) and not data.registry.url_is_approved(
                source_url, organization_id
            ):
                issues.append(f"proposal {proposal_id}: accepted.source_url 不属于批准域名")


def _validate_report_proposals(data: RepositoryData, issues: list[str]) -> None:
    ids = [item.get("id") for item in data.report_proposals]
    if len(ids) != len(set(ids)):
        issues.append("reports/pending: report proposal id 重复")
    blocked = _blocked_scopes(data)
    mentor_ids = {item.get("id") for item in data.mentors}
    for proposal in data.report_proposals:
        proposal_id = proposal.get("id")
        reporter_id = proposal.get("reporter", {}).get("github_user_id")
        if "report" in blocked.get(reporter_id, set()):
            issues.append(f"report proposal {proposal_id}: 封禁用户仍有待审核反馈")
        if proposal.get("mentor_id") not in mentor_ids:
            issues.append(f"report proposal {proposal_id}: mentor_id 不存在")


def _validate_organization_review_resolutions(
    data: RepositoryData,
    issues: list[str],
) -> None:
    ids = [item.get("id") for item in data.organization_review_resolutions]
    if len(ids) != len(set(ids)):
        issues.append("reviews/resolutions: organization review id 重复")
    issue_numbers = [
        item.get("issue", {}).get("number")
        for item in data.organization_review_resolutions
        if isinstance(item.get("issue"), dict)
    ]
    if len(issue_numbers) != len(set(issue_numbers)):
        issues.append("reviews/resolutions: 批量投稿 Issue 重复")
    organization_ids = set(data.registry.by_id)
    for resolution in data.organization_review_resolutions:
        resolution_id = resolution.get("id", "<unknown>")
        created = set(resolution.get("created_organization_ids", []))
        updated = set(resolution.get("updated_organization_ids", []))
        missing = sorted((created | updated) - organization_ids)
        if missing:
            issues.append(
                f"organization review {resolution_id}: 机构不存在：{', '.join(missing)}"
            )
        mapped = set(resolution.get("mapped_proposal_ids", []))
        rejected = set(resolution.get("rejected_proposal_ids", []))
        if mapped & rejected:
            issues.append(f"organization review {resolution_id}: 同一提案不能同时映射和拒绝")


def _validate_revocations(data: RepositoryData, issues: list[str]) -> None:
    document = data.revocations
    if document.get("schema_version") != 1:
        issues.append("registry/revocations.yml: schema_version 必须为 1")
    seen: set[str] = set()
    for item in document.get("revocations", []):
        revocation_id = item.get("id")
        if not isinstance(revocation_id, str) or not revocation_id:
            issues.append("registry/revocations.yml: revocation 必须有 id")
        elif revocation_id in seen:
            issues.append(f"registry/revocations.yml: id {revocation_id} 重复")
        seen.add(revocation_id)
