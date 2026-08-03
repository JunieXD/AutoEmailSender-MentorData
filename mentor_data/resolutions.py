from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import SubmissionError
from .io_utils import load_json, write_json_atomic, write_yaml_atomic
from .normalization import (
    is_generic_email,
    is_valid_email,
    normalize_email,
    normalize_name_key,
    normalized_web_url,
)
from .repository import load_repository

HIDDEN_LIFECYCLE_STATUSES = {"retired", "departed", "deceased", "removed"}
SUPPORTED_STATUS_VALUES = {
    "active",
    "retired",
    "departed",
    "deceased",
    "stale",
    "disputed",
    "removed",
}


def _date_from_datetime(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date().isoformat()


def _add_resolution_source(item: dict[str, Any], resolution_id: str) -> None:
    values = item.setdefault("resolution_ids", [])
    if resolution_id not in values:
        values.append(resolution_id)


def _apply_lifecycle(
    mentor: dict[str, Any],
    accepted: dict[str, Any],
    reason: str,
    resolution_id: str,
) -> None:
    status = accepted.get("status")
    if status not in SUPPORTED_STATUS_VALUES:
        raise SubmissionError("Resolution accepted.status 无效")
    source_url = normalized_web_url(accepted.get("status_source_url"))
    observed_at = accepted.get("status_observed_at")
    if source_url is None or not isinstance(observed_at, str):
        raise SubmissionError("生命周期修改需要 HTTP(S) 证据和 status_observed_at")

    mentor["status"] = status
    mentor["status_reason"] = accepted.get("status_reason") or reason
    mentor["status_source_url"] = source_url
    mentor["status_observed_at"] = observed_at
    mentor["updated_at"] = observed_at
    mentor["last_verified_at"] = observed_at

    if status not in HIDDEN_LIFECYCLE_STATUSES:
        return
    ended_at = _date_from_datetime(observed_at)
    for affiliation in mentor.get("affiliations", []):
        if affiliation.get("status") == "current":
            affiliation["status"] = "former"
            affiliation["is_primary"] = False
            affiliation["ended_at"] = affiliation.get("ended_at") or ended_at
            _add_resolution_source(affiliation, resolution_id)
    for contact in mentor.get("contacts", []):
        if contact.get("status") == "current":
            contact["status"] = "former"
            contact["is_primary"] = False
            _add_resolution_source(contact, resolution_id)
    for profile in mentor.get("profiles", []):
        if profile.get("status") == "current":
            profile["status"] = "former"
            _add_resolution_source(profile, resolution_id)


def _source_fields(
    existing: dict[str, Any] | None,
    *,
    resolution_id: str,
    changed: bool,
) -> dict[str, list[str]]:
    claim_ids = list(existing.get("claim_ids", [])) if existing else []
    resolution_ids = list(existing.get("resolution_ids", [])) if existing else []
    if changed and resolution_id not in resolution_ids:
        resolution_ids.append(resolution_id)
    return {"claim_ids": claim_ids, "resolution_ids": resolution_ids}


def _replace_names(mentor: dict[str, Any], specs: list[dict[str, Any]], resolution_id: str) -> None:
    existing_by_key = {
        (normalize_name_key(item["value"]), item["kind"]): item for item in mentor.get("names", [])
    }
    names: list[dict[str, Any]] = []
    for spec in specs:
        core = {
            "value": spec["value"],
            "kind": spec["kind"],
            "is_primary": spec["is_primary"],
        }
        existing = existing_by_key.get((normalize_name_key(spec["value"]), spec["kind"]))
        changed = existing is None or any(existing.get(key) != value for key, value in core.items())
        names.append(
            {
                **core,
                **_source_fields(existing, resolution_id=resolution_id, changed=changed),
            }
        )
    mentor["names"] = names


def _replace_contacts(
    mentor: dict[str, Any], specs: list[dict[str, Any]], resolution_id: str
) -> None:
    existing_by_email = {item["normalized_value"]: item for item in mentor.get("contacts", [])}
    contacts: list[dict[str, Any]] = []
    for spec in specs:
        email = normalize_email(spec["value"])
        if not is_valid_email(email):
            raise SubmissionError(f"纠错后的邮箱格式无效：{spec['value']}")
        if is_generic_email(email) and spec["status"] not in {"generic", "shared"}:
            raise SubmissionError("通用邮箱只能标记为 generic/shared")
        source_url = normalized_web_url(spec["source_url"])
        if source_url is None:
            raise SubmissionError("纠错后的邮箱来源 URL 无效")
        core = {
            "type": "email",
            "value": email,
            "normalized_value": email,
            "status": spec["status"],
            "is_primary": spec["is_primary"],
            "affiliation_id": spec["affiliation_id"],
            "source_url": source_url,
            "observed_at": spec["observed_at"],
        }
        existing = existing_by_email.get(email)
        changed = existing is None or any(existing.get(key) != value for key, value in core.items())
        contacts.append(
            {
                **core,
                **_source_fields(existing, resolution_id=resolution_id, changed=changed),
            }
        )
    mentor["contacts"] = contacts


def _replace_affiliations(
    mentor: dict[str, Any], specs: list[dict[str, Any]], resolution_id: str
) -> None:
    existing_by_id = {item["id"]: item for item in mentor.get("affiliations", [])}
    affiliations: list[dict[str, Any]] = []
    for spec in specs:
        source_url = normalized_web_url(spec["source_url"])
        if source_url is None:
            raise SubmissionError("纠错后的任职来源 URL 无效")
        core = {**spec, "source_url": source_url}
        existing = existing_by_id.get(spec["id"])
        changed = existing is None or any(existing.get(key) != value for key, value in core.items())
        affiliations.append(
            {
                **core,
                **_source_fields(existing, resolution_id=resolution_id, changed=changed),
            }
        )
    mentor["affiliations"] = affiliations


def _replace_profiles(
    mentor: dict[str, Any], specs: list[dict[str, Any]], resolution_id: str
) -> None:
    existing_by_url = {item["url"]: item for item in mentor.get("profiles", [])}
    profiles: list[dict[str, Any]] = []
    for spec in specs:
        url = normalized_web_url(spec["url"])
        if url is None:
            raise SubmissionError("纠错后的主页 URL 无效")
        core = {**spec, "url": url}
        existing = existing_by_url.get(url)
        changed = existing is None or any(existing.get(key) != value for key, value in core.items())
        profiles.append(
            {
                **core,
                **_source_fields(existing, resolution_id=resolution_id, changed=changed),
            }
        )
    mentor["profiles"] = profiles


def _record_field_source(mentor: dict[str, Any], field: str, resolution_id: str) -> None:
    sources = mentor.setdefault("field_provenance", {}).setdefault(field, [])
    if resolution_id not in sources:
        sources.append(resolution_id)


def _apply_structured_patch(
    mentor: dict[str, Any],
    accepted: dict[str, Any],
    resolution: dict[str, Any],
) -> None:
    resolution_id = resolution["id"]
    if resolution_id not in mentor.setdefault("resolution_ids", []):
        mentor["resolution_ids"].append(resolution_id)
    replacements = {
        "names": _replace_names,
        "contacts": _replace_contacts,
        "affiliations": _replace_affiliations,
        "profiles": _replace_profiles,
    }
    for field, replacement in replacements.items():
        if field in accepted:
            replacement(mentor, accepted[field], resolution_id)
            _record_field_source(mentor, field, resolution_id)
    for field in ("title", "research_directions", "recent_papers"):
        if field in accepted:
            mentor[field] = copy.deepcopy(accepted[field])
            _record_field_source(mentor, field, resolution_id)
    if "status" in accepted:
        _apply_lifecycle(mentor, accepted, resolution["reason"], resolution_id)
        _record_field_source(mentor, "status", resolution_id)
    mentor["updated_at"] = resolution["decided_at"]
    mentor["last_verified_at"] = resolution["decided_at"]


def apply_resolution(root: Path, resolution_path: Path) -> tuple[Path, Path | None]:
    data = load_repository(root, validate=True)
    resolution = load_json(resolution_path)
    schema = load_json(data.root / "schemas" / "resolution.schema.json")
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(resolution)
    )
    if errors:
        raise SubmissionError(f"纠错裁决无效：{errors[0].message}")
    if resolution["decision"] in {"accepted", "partially_accepted"}:
        patch_schema = load_json(data.root / "schemas" / "correction-patch.schema.json")
        patch_errors = list(
            Draft202012Validator(patch_schema, format_checker=FormatChecker()).iter_errors(
                resolution["accepted"]
            )
        )
        if patch_errors:
            raise SubmissionError(f"纠错补丁无效：{patch_errors[0].message}")
    elif resolution["accepted"]:
        raise SubmissionError("未接受的纠错裁决不得包含 accepted 修改")
    mentor_id = resolution["mentor_id"]
    mentor_path = data.mentor_paths.get(mentor_id)
    if mentor_path is None:
        raise SubmissionError("纠错裁决引用的导师不存在")

    destination = data.root / "reports" / "resolutions" / f"{resolution['id']}.json"
    existing = next((item for item in data.resolutions if item["id"] == resolution["id"]), None)
    if existing is not None and existing != resolution:
        raise SubmissionError("相同 Resolution ID 已存在但内容不同")

    changed_mentor_path: Path | None = None
    if resolution["decision"] in {"accepted", "partially_accepted"}:
        mentor = copy.deepcopy(next(item for item in data.mentors if item["id"] == mentor_id))
        accepted = resolution["accepted"]
        _apply_structured_patch(mentor, accepted, resolution)
        write_json_atomic(mentor_path, mentor)
        changed_mentor_path = mentor_path

        if mentor["status"] != "active":
            revocations = copy.deepcopy(data.revocations)
            events = revocations.setdefault("revocations", [])
            event_id = f"revocation_{resolution['id']}"
            if not any(item.get("id") == event_id for item in events):
                events.append(
                    {
                        "id": event_id,
                        "community_record_id": mentor_id,
                        "status": mentor["status"],
                        "reason": mentor.get("status_reason"),
                        "source_url": mentor.get("status_source_url"),
                        "observed_at": mentor.get("status_observed_at"),
                        "resolution_id": resolution["id"],
                    }
                )
                write_yaml_atomic(data.root / "registry" / "revocations.yml", revocations)

    write_json_atomic(destination, resolution)
    load_repository(root, validate=True)
    return destination, changed_mentor_path
