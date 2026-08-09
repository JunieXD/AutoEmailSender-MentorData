from __future__ import annotations

import hashlib
import re
import shutil
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from .io_utils import json_bytes, load_json, write_json_atomic
from .repository import RepositoryData, load_repository

DATASET_SCHEMA_VERSION = 2
DATASET_FORMAT_ID = "mentor-data-content-addressed-v2.1"
DATASET_VERSION_PATTERN = re.compile(r"^v2-[a-f0-9]{32}$")
PUBLICATION_ARCHIVE_ROOTS = {".git", "datasets", "objects", "releases"}
CONTENT_OBJECT_PATTERN = re.compile(r"^objects/sha256/(?P<digest>[a-f0-9]{64})\.json$")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_digest(
    data: RepositoryData,
    *,
    automatically_stale_ids: set[str],
) -> str:
    payload = {
        "format": DATASET_FORMAT_ID,
        "policy": data.policy,
        "organizations": data.organizations_document,
        "mentors": sorted(data.mentors, key=lambda item: item["id"]),
        "claims": sorted(data.claims, key=lambda item: item["id"]),
        "resolutions": sorted(data.resolutions, key=lambda item: item["id"]),
        "revocations": data.revocations,
        "automatically_stale_ids": sorted(automatically_stale_ids),
    }
    return hashlib.sha256(json_bytes(payload, pretty=False)).hexdigest()


def _automatic_stale_deadline(
    data: RepositoryData,
    mentor: dict[str, Any],
) -> datetime | None:
    stale_after_days = data.policy.get("publication", {}).get("stale_after_days")
    # Older canonical records may predate last_verified_at. Treat their last
    # canonical update as the verification baseline instead of allowing an
    # indefinitely active record to bypass the publication freshness policy.
    last_verified_at = mentor.get("last_verified_at")
    if not isinstance(last_verified_at, str):
        last_verified_at = mentor.get("updated_at") or mentor.get("created_at")
    if not isinstance(stale_after_days, int) or stale_after_days <= 0:
        raise ValueError("publication.stale_after_days 必须为正整数")
    if not isinstance(last_verified_at, str):
        return None
    parsed = datetime.fromisoformat(last_verified_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC) + timedelta(days=stale_after_days)


def _primary(values: list[dict[str, Any]], *, current: bool = False) -> dict[str, Any] | None:
    candidates = values
    if current:
        candidates = [item for item in values if item.get("status") == "current"]
    return next((item for item in candidates if item.get("is_primary")), None)


def _relocation_available_at(
    data: RepositoryData,
    source_ids: set[str],
    *,
    observed_at: str,
) -> str:
    """Return the v2-compatible time when a transfer became publishable."""

    parsed_observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    candidates = [
        parsed_observed_at.replace(
            tzinfo=parsed_observed_at.tzinfo or UTC,
        ).astimezone(UTC)
    ]
    for claim in data.claims:
        if claim.get("id") not in source_ids:
            continue
        value = claim.get("moderation", {}).get("decision_at")
        if not isinstance(value, str):
            continue
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        candidates.append(parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC))
    for resolution in data.resolutions:
        if resolution.get("id") not in source_ids:
            continue
        value = resolution.get("decided_at")
        if not isinstance(value, str):
            continue
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        candidates.append(parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC))
    return _iso_utc(max(candidates))


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


def _discovery_source_url(
    claim_by_id: dict[str, dict[str, Any]],
    primary_contact: dict[str, Any],
    primary_affiliation: dict[str, Any],
) -> str:
    organization_id = primary_affiliation["organization_id"]
    for claim_id in reversed(primary_contact.get("claim_ids", [])):
        claim = claim_by_id.get(claim_id)
        if claim is None or claim.get("status") not in {"accepted", "partially_accepted"}:
            continue
        accepted = claim.get("accepted", {})
        if accepted.get("organization_id") != organization_id:
            continue
        source_url = accepted.get("source_url")
        if isinstance(source_url, str) and source_url:
            return source_url
    return primary_affiliation["source_url"]


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
    if primary_name is None or primary_contact is None or primary_affiliation is None:
        raise ValueError(f"导师 {mentor['id']} 缺少发布所需主要字段")
    current_profile = next(
        (
            item
            for item in mentor["profiles"]
            if item.get("status") == "current"
            and item.get("affiliation_id") == primary_affiliation["id"]
        ),
        None,
    ) or next(
        (
            item
            for item in mentor["profiles"]
            if item.get("status") == "current"
            and item.get("affiliation_id") is None
        ),
        None,
    )
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
        "title": primary_affiliation.get("title") or mentor.get("title"),
        "university": names["university"],
        "school": names["school"],
        "department": names["department"],
        "research_direction": "；".join(mentor.get("research_directions", [])) or None,
        "recent_papers": mentor.get("recent_papers", []),
        "profile_url": current_profile["url"] if current_profile else None,
        "source_url": _discovery_source_url(
            claim_by_id,
            primary_contact,
            primary_affiliation,
        ),
        "status": mentor["status"],
        "last_verified_at": mentor.get("last_verified_at"),
        "contacts": active_contacts,
        "affiliations": active_affiliations,
        "contributors": _claim_contributors(claim_by_id, mentor["claim_ids"]),
    }


def _relocation_events(data: RepositoryData, mentor: dict[str, Any]) -> list[dict[str, Any]]:
    """Project active transfers into the backward-compatible revocation event stream."""

    primary_affiliation = _primary(mentor.get("affiliations", []), current=True)
    if mentor.get("status") != "active" or primary_affiliation is None:
        return []
    primary_sources = {
        *primary_affiliation.get("claim_ids", []),
        *primary_affiliation.get("resolution_ids", []),
    }
    if not primary_sources:
        return []
    destination_names = data.registry.projection_names(
        primary_affiliation["organization_id"]
    )
    destination_label = " / ".join(
        value
        for value in (
            destination_names["university"],
            destination_names["school"],
            destination_names["department"],
        )
        if value
    )
    events: list[dict[str, Any]] = []
    for former in mentor.get("affiliations", []):
        if (
            former.get("status") != "former"
            or former.get("organization_id") == primary_affiliation["organization_id"]
        ):
            continue
        former_sources = {
            *former.get("claim_ids", []),
            *former.get("resolution_ids", []),
        }
        transfer_sources = primary_sources.intersection(former_sources)
        if not transfer_sources:
            continue
        origin_names = data.registry.projection_names(former["organization_id"])
        origin_label = " / ".join(
            value
            for value in (
                origin_names["university"],
                origin_names["school"],
                origin_names["department"],
            )
            if value
        )
        source_observed_at = primary_affiliation["observed_at"]
        event_observed_at = _relocation_available_at(
            data,
            transfer_sources,
            observed_at=source_observed_at,
        )
        seed = (
            f"{mentor['id']}:{former['organization_id']}:"
            f"{primary_affiliation['organization_id']}:{source_observed_at}"
        )
        event_id = f"relocation_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"
        reason = f"导师任职已从「{origin_label}」调动至「{destination_label}」"
        if len(reason) > 1_000:
            reason = f"{reason[:999]}…"
        events.append(
            {
                "kind": "relocation",
                "id": event_id,
                "community_record_id": mentor["id"],
                "status": "relocated",
                "from_organization_id": former["organization_id"],
                "to_organization_id": primary_affiliation["organization_id"],
                "reason": reason,
                "source_url": primary_affiliation["source_url"],
                "observed_at": event_observed_at,
            }
        )
    return sorted(events, key=lambda item: item["id"])


def _write_dataset_file(base: Path, relative_path: str, value: Any) -> dict[str, Any]:
    path = base / relative_path
    write_json_atomic(path, value)
    payload = path.read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _write_content_object(output_root: Path, value: Any) -> dict[str, Any]:
    payload = json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    relative_path = f"objects/sha256/{digest}.json"
    path = output_root / relative_path
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"内容寻址对象与其 SHA-256 不一致：{relative_path}")
    else:
        write_json_atomic(path, value)
    return {"path": relative_path, "sha256": digest, "bytes": len(payload)}


def _publish_static_files(data: RepositoryData, output_root: Path) -> None:
    site_root = data.root / "site"
    resolved_site_root = site_root.resolve()
    if output_root == data.root.resolve() or output_root == resolved_site_root:
        raise ValueError("数据发布目录不能覆盖仓库或静态页面源目录")

    for existing in sorted(output_root.iterdir()):
        if existing.name in PUBLICATION_ARCHIVE_ROOTS:
            continue
        if existing.is_dir() and not existing.is_symlink():
            shutil.rmtree(existing)
        else:
            existing.unlink()

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


def _existing_release(
    output_root: Path,
    *,
    version: str,
    digest: str,
) -> dict[str, Any] | None:
    release_root = output_root / "releases" / version
    if not release_root.exists():
        return None
    manifest = load_json(release_root / "manifest.json")
    catalog = load_json(release_root / "catalog.json")
    if (
        manifest.get("schema_version") != DATASET_SCHEMA_VERSION
        or manifest.get("dataset_version") != version
        or manifest.get("source_sha256") != digest
        or catalog.get("schema_version") != DATASET_SCHEMA_VERSION
        or catalog.get("dataset_version") != version
        or manifest.get("generated_at") != catalog.get("generated_at")
    ):
        raise ValueError(f"现有数据发布目录与内容摘要不一致：{release_root}")
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": version,
        "generated_at": catalog["generated_at"],
        "manifest_path": f"releases/{version}/manifest.json",
        "catalog_path": f"releases/{version}/catalog.json",
    }


def build_dataset(
    root: Path,
    output_root: Path,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    data = load_repository(root, validate=True)
    instant = (generated_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    stale_deadlines = {
        mentor["id"]: deadline
        for mentor in data.mentors
        if mentor.get("status") == "active"
        and (deadline := _automatic_stale_deadline(data, mentor)) is not None
    }
    automatically_stale_ids = {
        mentor_id
        for mentor_id, deadline in stale_deadlines.items()
        if instant >= deadline
    }
    digest = _source_digest(data, automatically_stale_ids=automatically_stale_ids)
    version = f"v2-{digest[:32]}"

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    existing = _existing_release(output_root, version=version, digest=digest)
    if existing is not None:
        _publish_static_files(data, output_root)
        write_json_atomic(output_root / "latest.json", existing)
        return existing
    release_root = output_root / "releases" / version
    release_root.mkdir(parents=True, exist_ok=False)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    hidden_status_records: list[dict[str, Any]] = []
    relocation_events: list[dict[str, Any]] = []
    claim_by_id = {item["id"]: item for item in data.claims}
    for mentor in sorted(data.mentors, key=lambda item: item["id"]):
        automatically_stale = mentor["id"] in automatically_stale_ids
        if mentor["status"] != "active" or automatically_stale:
            stale_deadline = stale_deadlines.get(mentor["id"])
            hidden_status_records.append(
                {
                    "community_record_id": mentor["id"],
                    "status": "stale" if automatically_stale else mentor["status"],
                    "reason": (
                        f"超过 {data.policy['publication']['stale_after_days']} 天未重新核验"
                        if automatically_stale
                        else mentor.get("status_reason")
                    ),
                    "source_url": mentor.get("status_source_url"),
                    "observed_at": (
                        _iso_utc(stale_deadline)
                        if automatically_stale and stale_deadline is not None
                        else mentor.get("status_observed_at")
                    ),
                }
            )
            continue
        projection = _project_mentor(data, mentor, claim_by_id)
        relocation_events.extend(_relocation_events(data, mentor))
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
        shard = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "university": {"id": university_id, "name": university["canonical_name"]},
            "unit": {"id": unit_id, "name": unit["canonical_name"], "type": unit["type"]},
            "records": mentors,
        }
        shard_file = _write_content_object(output_root, shard)
        files.append(shard_file)
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
                "path": shard_file["path"],
            }
        )

    catalog = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "record_count": sum(len(items) for items in grouped.values()),
        "universities": [catalog_universities[key] for key in sorted(catalog_universities)],
    }
    catalog_path = f"releases/{version}/catalog.json"
    files.append(_write_dataset_file(output_root, catalog_path, catalog))

    existing_events = list(data.revocations.get("revocations", []))
    existing_event_ids = {item.get("id") for item in existing_events}
    revocations = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "records": hidden_status_records,
        "events": [
            *existing_events,
            *(
                event
                for event in relocation_events
                if event["id"] not in existing_event_ids
            ),
        ],
    }
    revocations_path = f"releases/{version}/revocations.json"
    files.append(_write_dataset_file(output_root, revocations_path, revocations))

    manifest = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "source_sha256": digest,
        "minimum_app_version": data.policy["minimum_app_version"],
        "files": sorted(files, key=lambda item: item["path"]),
    }
    manifest_path = f"releases/{version}/manifest.json"
    write_json_atomic(output_root / manifest_path, manifest)

    _publish_static_files(data, output_root)

    latest = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": version,
        "generated_at": generated_at_text,
        "manifest_path": manifest_path,
        "catalog_path": catalog_path,
    }
    write_json_atomic(output_root / "latest.json", latest)
    return latest


def _safe_published_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"发布文件路径不安全：{value}")
    return path


def stage_current_dataset(archive_root: Path, output_root: Path) -> dict[str, Any]:
    archive_root = archive_root.resolve()
    output_root = output_root.resolve()
    if (
        output_root == archive_root
        or archive_root in output_root.parents
        or output_root in archive_root.parents
    ):
        raise ValueError("部署输出目录与发布归档不能互相包含")

    latest = load_json(archive_root / "latest.json")
    if not isinstance(latest, dict):
        raise ValueError("当前发布索引不是受支持的内容寻址格式")
    version = latest.get("dataset_version")
    if (
        latest.get("schema_version") != DATASET_SCHEMA_VERSION
        or not isinstance(version, str)
        or DATASET_VERSION_PATTERN.fullmatch(version) is None
        or latest.get("manifest_path") != f"releases/{version}/manifest.json"
        or latest.get("catalog_path") != f"releases/{version}/catalog.json"
    ):
        raise ValueError("当前发布索引不是受支持的内容寻址格式")
    manifest_path = _safe_published_path(latest["manifest_path"])
    manifest = load_json(archive_root / Path(*manifest_path.parts))
    if not isinstance(manifest, dict):
        raise ValueError("当前发布 Manifest 与 latest.json 不一致")
    if (
        manifest.get("schema_version") != DATASET_SCHEMA_VERSION
        or manifest.get("dataset_version") != version
        or not isinstance(manifest.get("source_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", manifest["source_sha256"]) is None
        or manifest["source_sha256"][:32] != version.removeprefix("v2-")
        or not isinstance(manifest.get("files"), list)
    ):
        raise ValueError("当前发布 Manifest 与 latest.json 不一致")

    required_paths = {manifest_path}
    declared_paths: set[PurePosixPath] = set()
    for item in manifest["files"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or item["bytes"] <= 0
        ):
            raise ValueError("Manifest 文件项无效")
        relative_path = _safe_published_path(item["path"])
        object_match = CONTENT_OBJECT_PATTERN.fullmatch(item["path"])
        allowed_release_paths = {
            f"releases/{version}/catalog.json",
            f"releases/{version}/revocations.json",
        }
        if object_match is None and item["path"] not in allowed_release_paths:
            raise ValueError(f"Manifest 包含当前版本之外的文件：{item['path']}")
        if object_match is not None and object_match.group("digest") != item["sha256"]:
            raise ValueError(f"内容寻址对象路径与摘要不一致：{item['path']}")
        if relative_path in declared_paths:
            raise ValueError(f"Manifest 包含重复文件：{item['path']}")
        declared_paths.add(relative_path)
        required_paths.add(relative_path)
        source = archive_root / Path(*relative_path.parts)
        payload = source.read_bytes()
        if len(payload) != item.get("bytes") or hashlib.sha256(payload).hexdigest() != item.get(
            "sha256"
        ):
            raise ValueError(f"发布归档文件校验失败：{item['path']}")
    required_release_paths = {
        PurePosixPath(f"releases/{version}/catalog.json"),
        PurePosixPath(f"releases/{version}/revocations.json"),
    }
    if not required_release_paths.issubset(declared_paths):
        raise ValueError("Manifest 缺少当前版本的目录或撤销记录")

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    for source in sorted(archive_root.iterdir()):
        if source.name in {".git", "datasets", "objects", "releases"}:
            continue
        destination = output_root / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    for relative_path in sorted(required_paths, key=str):
        source = archive_root / Path(*relative_path.parts)
        if not source.is_file():
            raise FileNotFoundError(f"当前发布缺少文件：{relative_path}")
        destination = output_root / Path(*relative_path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return latest
