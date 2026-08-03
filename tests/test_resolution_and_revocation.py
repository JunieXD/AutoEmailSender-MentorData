from __future__ import annotations

import copy
import json

import pytest

from mentor_data.io_utils import load_yaml
from mentor_data.repository import load_repository
from mentor_data.resolutions import apply_resolution
from mentor_data.revocation import revoke_contributor

from .helpers import build_test_repository, claim, mentor, save_claim, save_mentor


def _seed_two_claims(root) -> None:
    first = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=1,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
        profile_url="https://cs.example.edu/faculty/mentor",
    )
    second = copy.deepcopy(first)
    second["id"] = "claim_fixture_2002"
    second["contributor"]["github_user_id"] = 2002
    second["contributor"]["github_login_at_submission"] = "fixture-two"
    second["contributor"]["issue_number"] = 2
    second["contributor"]["issue_url"] = "https://github.com/example/repository/issues/2"
    save_claim(root, first)
    save_claim(root, second)
    value = mentor(claim_ids=["claim_fixture_1001", "claim_fixture_2002"])
    for item in [*value["names"], *value["contacts"], *value["affiliations"], *value["profiles"]]:
        item["claim_ids"] = ["claim_fixture_1001", "claim_fixture_2002"]
    for field in value["field_provenance"]:
        value["field_provenance"][field] = ["claim_fixture_1001", "claim_fixture_2002"]
    save_mentor(root, value)


@pytest.mark.parametrize("lifecycle_status", ["retired", "departed"])
def test_lifecycle_resolution_hides_current_contacts_and_affiliations(
    tmp_path, lifecycle_status: str
) -> None:
    root = build_test_repository(tmp_path)
    first = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=1,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, first)
    save_mentor(root, mentor())
    resolution_path = tmp_path / "retirement.json"
    resolution_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": f"resolution_{lifecycle_status}_001",
                "mentor_id": "mentor_fixture_0001",
                "report_issue": {
                    "number": 20,
                    "url": "https://github.com/example/repository/issues/20",
                },
                "reporter": {"github_user_id": 5005, "github_login": "reporter"},
                "decision": "accepted",
                "before": {"status": "active"},
                "proposed": {"status": lifecycle_status},
                "accepted": {
                    "status": lifecycle_status,
                    "status_reason": "官网确认生命周期变化",
                    "status_source_url": "https://cs.example.edu/faculty/mentor",
                    "status_observed_at": "2026-08-03T01:00:00Z",
                },
                "evidence_urls": ["https://cs.example.edu/faculty/mentor"],
                "moderator": {"github_user_id": 999, "github_login": "maintainer"},
                "decided_at": "2026-08-03T02:00:00Z",
                "reason": "官方页面已明确标注生命周期变化",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    apply_resolution(root, resolution_path)
    data = load_repository(root)
    value = data.mentors[0]
    assert value["status"] == lifecycle_status
    assert not any(item["status"] == "current" for item in value["contacts"])
    assert not any(item["status"] == "current" for item in value["affiliations"])
    revocations = load_yaml(root / "registry" / "revocations.yml")
    assert revocations["revocations"][0]["status"] == lifecycle_status


def test_structured_correction_supports_multiple_emails_and_dual_affiliation(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    first = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=1,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
        profile_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, first)
    save_mentor(root, mentor())
    resolution_path = tmp_path / "multi-affiliation.json"
    resolution_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "resolution_multi_affiliation_001",
                "mentor_id": "mentor_fixture_0001",
                "report_issue": {
                    "number": 22,
                    "url": "https://github.com/example/repository/issues/22",
                },
                "reporter": {"github_user_id": 5005, "github_login": "reporter"},
                "decision": "accepted",
                "before": {"email": "mentor@example.edu"},
                "proposed": {"value": "新增双聘邮箱", "explanation": "官网已公布"},
                "accepted": {
                    "affiliations": [
                        {
                            "id": "aff_fixture_primary",
                            "organization_id": "org_example_cs",
                            "status": "current",
                            "is_primary": True,
                            "title": "教授",
                            "started_at": None,
                            "ended_at": None,
                            "source_url": "https://cs.example.edu/faculty/mentor",
                            "observed_at": "2026-08-03T01:00:00Z",
                        },
                        {
                            "id": "aff_resolution_secondary",
                            "organization_id": "org_sample_ai",
                            "status": "current",
                            "is_primary": False,
                            "title": "教授",
                            "started_at": "2026-08-01",
                            "ended_at": None,
                            "source_url": "https://ai.sample.edu/faculty/mentor",
                            "observed_at": "2026-08-03T01:00:00Z",
                        },
                    ],
                    "contacts": [
                        {
                            "value": "mentor@example.edu",
                            "status": "current",
                            "is_primary": True,
                            "affiliation_id": "aff_fixture_primary",
                            "source_url": "https://cs.example.edu/faculty/mentor",
                            "observed_at": "2026-08-03T01:00:00Z",
                        },
                        {
                            "value": "mentor@sample.edu",
                            "status": "current",
                            "is_primary": False,
                            "affiliation_id": "aff_resolution_secondary",
                            "source_url": "https://ai.sample.edu/faculty/mentor",
                            "observed_at": "2026-08-03T01:00:00Z",
                        },
                    ],
                    "profiles": [
                        {
                            "url": "https://cs.example.edu/faculty/mentor",
                            "status": "current",
                            "affiliation_id": "aff_fixture_primary",
                            "observed_at": "2026-08-03T01:00:00Z",
                        },
                        {
                            "url": "https://ai.sample.edu/faculty/mentor",
                            "status": "current",
                            "affiliation_id": "aff_resolution_secondary",
                            "observed_at": "2026-08-03T01:00:00Z",
                        },
                    ],
                },
                "evidence_urls": [
                    "https://cs.example.edu/faculty/mentor",
                    "https://ai.sample.edu/faculty/mentor",
                ],
                "moderator": {"github_user_id": 999, "github_login": "maintainer"},
                "decided_at": "2026-08-03T02:00:00Z",
                "reason": "两个学校官网均确认当前任职",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    apply_resolution(root, resolution_path)
    value = load_repository(root).mentors[0]
    assert len([item for item in value["contacts"] if item["status"] == "current"]) == 2
    assert len([item for item in value["affiliations"] if item["status"] == "current"]) == 2
    secondary = next(item for item in value["contacts"] if item["value"] == "mentor@sample.edu")
    assert secondary["resolution_ids"] == ["resolution_multi_affiliation_001"]


def test_revoking_one_of_two_independent_contributors_keeps_mentor(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    _seed_two_claims(root)
    preview = revoke_contributor(
        root,
        github_user_id=1001,
        reason_code="deliberate_fabrication",
        source_issue_url="https://github.com/example/repository/issues/30",
        block_scopes=["contribute"],
        apply=False,
    )
    assert preview["dry_run"] is True
    assert load_repository(root).mentors[0]["claim_ids"] == [
        "claim_fixture_1001",
        "claim_fixture_2002",
    ]

    result = revoke_contributor(
        root,
        github_user_id=1001,
        reason_code="deliberate_fabrication",
        source_issue_url="https://github.com/example/repository/issues/30",
        block_scopes=["contribute"],
        apply=True,
    )
    assert result["removed_mentor_ids"] == []
    data = load_repository(root)
    assert data.mentors[0]["claim_ids"] == ["claim_fixture_2002"]
    assert data.mentors[0]["contacts"][0]["claim_ids"] == ["claim_fixture_2002"]
    assert not (root / "claims" / "1001" / "claim_fixture_1001.json").exists()
    blocked = load_yaml(root / "registry" / "blocked-contributors.yml")
    assert blocked["blocked"][0]["github_user_id"] == 1001


def test_revoking_only_contributor_removes_current_mentor_entity(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    first = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=1,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, first)
    save_mentor(root, mentor())
    result = revoke_contributor(
        root,
        github_user_id=1001,
        reason_code="deliberate_fabrication",
        source_issue_url=None,
        block_scopes=[],
        apply=True,
    )
    assert result["removed_mentor_ids"] == ["mentor_fixture_0001"]
    data = load_repository(root)
    assert data.mentors == []
    assert data.claims == []
