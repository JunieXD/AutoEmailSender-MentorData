from __future__ import annotations

import copy
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mentor_data.io_utils import write_json_atomic, write_yaml_atomic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-03T00:00:00Z"


def build_test_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for directory in (
        "schemas",
        "site",
        "registry",
        "records/mentors",
        "claims",
        "reports/resolutions",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "schemas", root / "schemas", dirs_exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "site", root / "site", dirs_exist_ok=True)
    shutil.copy2(PROJECT_ROOT / "registry" / "policy.yml", root / "registry" / "policy.yml")
    write_yaml_atomic(
        root / "registry" / "blocked-contributors.yml", {"schema_version": 1, "blocked": []}
    )
    write_yaml_atomic(
        root / "registry" / "revocations.yml", {"schema_version": 1, "revocations": []}
    )
    write_yaml_atomic(
        root / "registry" / "organizations.yml",
        {
            "schema_version": 1,
            "organizations": [
                {
                    "id": "org_example_university",
                    "type": "university",
                    "canonical_name": "示例大学",
                    "parent_id": None,
                    "aliases": ["示大", "Example University"],
                    "official_urls": ["https://www.example.edu/"],
                    "approved_domains": ["example.edu"],
                    "status": "active",
                    "successor_id": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "org_example_cs",
                    "type": "school",
                    "canonical_name": "计算机学院",
                    "parent_id": "org_example_university",
                    "aliases": ["计算机系"],
                    "official_urls": ["https://cs.example.edu/"],
                    "approved_domains": [],
                    "status": "active",
                    "successor_id": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "org_sample_university",
                    "type": "university",
                    "canonical_name": "样本大学",
                    "parent_id": None,
                    "aliases": ["样大"],
                    "official_urls": ["https://www.sample.edu/"],
                    "approved_domains": ["sample.edu"],
                    "status": "active",
                    "successor_id": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": "org_sample_ai",
                    "type": "institute",
                    "canonical_name": "人工智能研究院",
                    "parent_id": "org_sample_university",
                    "aliases": ["AI研究院"],
                    "official_urls": ["https://ai.sample.edu/"],
                    "approved_domains": [],
                    "status": "active",
                    "successor_id": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        },
    )
    return root


def claim(
    *,
    claim_id: str,
    mentor_id: str,
    user_id: int,
    login: str,
    issue_number: int,
    name: str,
    email: str,
    organization_id: str,
    source_url: str,
    title: str | None = "教授",
    profile_url: str | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "email": email,
        "organization_id": organization_id,
        "submitted_university": None,
        "submitted_school": None,
        "submitted_department": None,
        "title": title,
        "research_directions": ["机器学习"],
        "recent_papers": ["A Safe Example Paper"],
        "profile_url": profile_url,
        "source_url": source_url,
        "mentor_status": "active",
    }
    return {
        "schema_version": 1,
        "id": claim_id,
        "mentor_id": mentor_id,
        "status": "accepted",
        "contributor": {
            "github_user_id": user_id,
            "github_login_at_submission": login,
            "github_user_type": "User",
            "issue_number": issue_number,
            "issue_url": f"https://github.com/example/repository/issues/{issue_number}",
            "submitted_at": NOW,
            "account_created_at": "2020-01-01T00:00:00Z",
            "account_age_days_at_submission": 2400,
        },
        "submitted": copy.deepcopy(payload),
        "accepted": copy.deepcopy(payload),
        "evidence": [
            {
                "fields": [
                    "name",
                    "email",
                    "organization_id",
                    "title",
                    "research_directions",
                    "recent_papers",
                ],
                "source_url": source_url,
                "observed_at": NOW,
            }
        ],
        "moderation": {
            "mode": "manual",
            "policy_version": 1,
            "normalized_fields": [],
            "moderator_github_user_id": 999,
            "reason": "fixture",
            "decision_at": NOW,
        },
        "revocation_reason": None,
        "revoked_at": None,
    }


def mentor(
    *,
    mentor_id: str = "mentor_fixture_0001",
    claim_ids: list[str] | None = None,
    status: str = "active",
    dual: bool = False,
) -> dict[str, Any]:
    resolved_claim_ids = claim_ids or ["claim_fixture_1001"]
    primary_claim = resolved_claim_ids[0]
    affiliations = [
        {
            "id": "aff_fixture_primary",
            "organization_id": "org_example_cs",
            "status": "current",
            "is_primary": True,
            "title": "教授",
            "started_at": None,
            "ended_at": None,
            "source_url": "https://cs.example.edu/faculty/mentor",
            "observed_at": NOW,
            "claim_ids": [primary_claim],
        }
    ]
    contacts = [
        {
            "type": "email",
            "value": "mentor@example.edu",
            "normalized_value": "mentor@example.edu",
            "status": "current",
            "is_primary": True,
            "affiliation_id": "aff_fixture_primary",
            "source_url": "https://cs.example.edu/faculty/mentor",
            "observed_at": NOW,
            "claim_ids": [primary_claim],
        }
    ]
    profiles = [
        {
            "url": "https://cs.example.edu/faculty/mentor",
            "status": "current",
            "affiliation_id": "aff_fixture_primary",
            "observed_at": NOW,
            "claim_ids": [primary_claim],
        }
    ]
    if dual:
        second_claim = resolved_claim_ids[1]
        affiliations.append(
            {
                "id": "aff_fixture_secondary",
                "organization_id": "org_sample_ai",
                "status": "current",
                "is_primary": False,
                "title": "教授",
                "started_at": None,
                "ended_at": None,
                "source_url": "https://ai.sample.edu/faculty/mentor",
                "observed_at": NOW,
                "claim_ids": [second_claim],
            }
        )
        contacts.append(
            {
                "type": "email",
                "value": "mentor@sample.edu",
                "normalized_value": "mentor@sample.edu",
                "status": "current",
                "is_primary": False,
                "affiliation_id": "aff_fixture_secondary",
                "source_url": "https://ai.sample.edu/faculty/mentor",
                "observed_at": NOW,
                "claim_ids": [second_claim],
            }
        )
        profiles.append(
            {
                "url": "https://ai.sample.edu/faculty/mentor",
                "status": "current",
                "affiliation_id": "aff_fixture_secondary",
                "observed_at": NOW,
                "claim_ids": [second_claim],
            }
        )
    return {
        "schema_version": 1,
        "id": mentor_id,
        "status": status,
        "status_reason": None,
        "status_source_url": "https://cs.example.edu/faculty/mentor",
        "status_observed_at": NOW,
        "names": [
            {
                "value": "示例导师",
                "kind": "native",
                "is_primary": True,
                "claim_ids": [primary_claim],
            }
        ],
        "contacts": contacts,
        "affiliations": affiliations,
        "profiles": profiles,
        "title": "教授",
        "research_directions": ["机器学习"],
        "recent_papers": ["A Safe Example Paper"],
        "claim_ids": resolved_claim_ids,
        "resolution_ids": [],
        "field_provenance": {
            "name": [primary_claim],
            "email": [primary_claim],
            "affiliations": resolved_claim_ids,
            "status": [primary_claim],
            "title": [primary_claim],
            "research_directions": [primary_claim],
            "recent_papers": [primary_claim],
            "profile_url": [primary_claim],
        },
        "created_at": NOW,
        "updated_at": NOW,
        "last_verified_at": NOW,
    }


def save_claim(root: Path, value: dict[str, Any]) -> Path:
    user_id = value["contributor"]["github_user_id"]
    path = root / "claims" / str(user_id) / f"{value['id']}.json"
    write_json_atomic(path, value)
    return path


def save_mentor(root: Path, value: dict[str, Any]) -> Path:
    path = root / "records" / "mentors" / f"{value['id']}.json"
    write_json_atomic(path, value)
    return path


def fixed_datetime() -> datetime:
    return datetime(2026, 8, 3, tzinfo=UTC)
