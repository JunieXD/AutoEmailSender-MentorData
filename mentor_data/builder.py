from __future__ import annotations

import hashlib
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io_utils import json_bytes, write_json_atomic
from .repository import RepositoryData, load_repository


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_digest(data: RepositoryData) -> str:
    payload = {
        "policy": data.policy,
        "organizations": data.organizations_document,
        "mentors": sorted(data.mentors, key=lambda item: item["id"]),
        "claims": sorted(data.claims, key=lambda item: item["id"]),
        "resolutions": sorted(data.resolutions, key=lambda item: item["id"]),
        "revocations": data.revocations,
    }
    return hashlib.sha256(json_bytes(payload, pretty=False)).hexdigest()


def _primary(values: list[dict[str, Any]], *, current: bool = False) -> dict[str, Any] | None:
    candidates = values
    if current:
        candidates = [item for item in values if item.get("status") == "current"]
    return next((item for item in candidates if item.get("is_primary")), None)


def _claim_contributors(
    claim_by_id: dict[str, dict[str, Any]], claim_ids: list[str]
) -> list[dict[str, Any]]:
    contributors: dict[int, dict[str, Any]] = {}
    for claim_id in claim_ids:
        claim = claim_by_id.get(claim_id)
        if claim is None or claim.get("status") not in {"accepted", "partially_accepted"}:
            continue
        contributor = claim["contributor"]
        user_id = contributor["github_user_id"]
        current = contributors.get(user_id)
        item = {
            "github_user_id": user_id,
            "github_login_at_submission": contributor["github_login_at_submission"],
            "issue_urls": [contributor["issue_url"]],
        }
        if current is None:
            contributors[user_id] = item
        elif contributor["issue_url"] not in current["issue_urls"]:
            current["issue_urls"].append(contributor["issue_url"])
    return [contributors[key] for key in sorted(contributors)]


def _project_affiliation(data: RepositoryData, affiliation: dict[str, Any]) -> dict[str, Any]:
    names = data.registry.projection_names(affiliation["organization_id"])
    return {
        "id": affiliation["id"],
        "organization_id": affiliation["organization_id"],
        "status": affiliation["status"],
        "is_primary": affiliation["is_primary"],
        "title": affiliation.get("title"),
        "university": names["university"],
        "school": names["school"],
        "department": names["department"],
        "source_url": affiliation["source_url"],
        "observed_at": affiliation["observed_at"],
    }


def _project_mentor(
    data: RepositoryData,
    mentor: dict[str, Any],
    claim_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    primary_name = _primary(mentor["names"])
    primary_contact = _primary(mentor["contacts"], current=True)
    primary_affiliation = _primary(mentor["affiliations"], current=True)
    current_profile = next(
        (item for item in mentor["profiles"] if item.get("status") == "current"),
        None,
    )
    if primary_name is None or primary_contact is None or primary_affiliation is None:
        raise ValueError(f"导师 {mentor['id']} 缺少发布所需主要字段")
    names = data.registry.projection_names(primary_affiliation["organization_id"])
    active_contacts = [
        {
            "email": item["normalized_value"],
            "is_primary": item["is_primary"],
            "affiliation_id": item.get("affiliation_id"),
            "source_url": item["source_url"],
            "observed_at": item["observed_at"],
        }
        for item in mentor["contacts"]
        if item.get("status") == "current"
    ]
    active_affiliations = [
        _project_affiliation(data, item)
        for item in mentor["affiliations"]
        if item.get("status") == "current"
    ]
    return {
        "id": mentor["id"],
        "name": primary_name["value"],
        "email": primary_contact["normalized_value"],
        "title": mentor.get("title") or primary_affiliation.get("title"),
        "university": names["university"],
        "school": names["school"],
        "department": names["department"],
        "research_direction": "；".join(mentor.get("research_directions", [])) or None,
        "recent_papers": mentor.get("recent_papers", []),
        "profile_url": current_profile["url"] if current_profile else None,
        "source_url": primary_contact["source_url"],
        "status": mentor["status"],
        "last_verified_at": mentor.get("last_verified_at"),
        "contacts": active_contacts,
        "affiliations": active_affiliations,
        "contributors": _claim_contributors(claim_by_id, mentor["claim_ids"]),
    }


def _write_dataset_file(base: Path, relative_path: str, value: Any) -> dict[str, Any]:
    path = base / relative_path
    write_json_atomic(path, value)
    payload = path.read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def build_dataset(
    root: Path,
    output_root: Path,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    data = load_repository(root, validate=True)
    instant = (generated_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    digest = _source_digest(data)
    version = f"{instant.strftime('%Y-%m-%dT%H%M%SZ')}-{digest[:12]}"

    output_root = output_root.resolve()
    dataset_root = output_root / "datasets" / version
    if dataset_root.exists():
        raise FileExistsError(f"数据集版本已经存在：{dataset_root}")
    dataset_root.mkdir(parents=True, exist_ok=False)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    hidden_status_records: list[dict[str, Any]] = []
    claim_by_id = {item["id"]: item for item in data.claims}
    for mentor in sorted(data.mentors, key=lambda item: item["id"]):
        if mentor["status"] != "active":
            hidden_status_records.append(
                {
                    "community_record_id": mentor["id"],
                    "status": mentor["status"],
                    "reason": mentor.get("status_reason"),
                    "source_url": mentor.get("status_source_url"),
                    "observed_at": mentor.get("status_observed_at"),
                }
            )
            continue
        projection = _project_mentor(data, mentor, claim_by_id)
        primary_affiliation = _primary(mentor["affiliations"], current=True)
        if primary_affiliation is None:
            raise ValueError(f"导师 {mentor['id']} 缺少主要任职")
        shard_key = data.registry.shard_ids(primary_affiliation["organization_id"])
        grouped[shard_key].append(projection)

    generated_at_text = _iso_utc(instant)
    files: list[dict[str, Any]] = []
    catalog_universities: dict[str, dict[str, Any]] = {}
    for (university_id, unit_id), mentors in sorted(grouped.items()):
        university = data.registry.by_id[university_id]
        unit = data.registry.by_id[unit_id]
        relative_path = f"data/{university_id}/{unit_id}.json"
        shard = {
            "schema_version": 1,
            "dataset_version": version,
            "generated_at": generated_at_text,
            "university": {"id": university_id, "name": university["canonical_name"]},
            "unit": {"id": unit_id, "name": unit["canonical_name"], "type": unit["type"]},
            "records": mentors,
        }
        files.append(_write_dataset_file(dataset_root, relative_path, shard))
        catalog_entry = catalog_universities.setdefault(
            university_id,
            {
                "id": university_id,
                "name": university["canonical_name"],
                "record_count": 0,
                "units": [],
            },
        )
        catalog_entry["record_count"] += len(mentors)
        catalog_entry["units"].append(
            {
                "id": unit_id,
                "name": unit["canonical_name"],
                "type": unit["type"],
                "record_count": len(mentors),
                "path": relative_path,
            }
        )

    catalog = {
        "schema_version": 1,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "record_count": sum(len(items) for items in grouped.values()),
        "universities": [catalog_universities[key] for key in sorted(catalog_universities)],
    }
    files.append(_write_dataset_file(dataset_root, "catalog.json", catalog))

    revocations = {
        "schema_version": 1,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "records": hidden_status_records,
        "events": data.revocations.get("revocations", []),
    }
    files.append(_write_dataset_file(dataset_root, "revocations.json", revocations))

    manifest = {
        "schema_version": 1,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "minimum_app_version": data.policy["minimum_app_version"],
        "files": sorted(files, key=lambda item: item["path"]),
    }
    write_json_atomic(dataset_root / "manifest.json", manifest)

    site_root = data.root / "site"
    if site_root.exists():
        for source in sorted(site_root.iterdir()):
            destination = output_root / source.name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)

    data_license = data.policy["data_license"]
    write_json_atomic(
        output_root / "license.json",
        {
            "schema_version": 1,
            "spdx_id": data_license["spdx_id"],
            "name": data_license["name"],
            "url": data_license["url"],
            "attribution": data_license["attribution"],
        },
    )

    latest = {
        "schema_version": 1,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "manifest_path": f"datasets/{version}/manifest.json",
        "catalog_path": f"datasets/{version}/catalog.json",
    }
    write_json_atomic(output_root / "latest.json", latest)
    return latest
