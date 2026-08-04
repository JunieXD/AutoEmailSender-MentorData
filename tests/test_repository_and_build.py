from __future__ import annotations

import json

import pytest

from mentor_data.builder import build_dataset
from mentor_data.errors import RepositoryValidationError
from mentor_data.io_utils import load_yaml, write_yaml_atomic
from mentor_data.repository import load_repository

from .helpers import (
    build_test_repository,
    claim,
    fixed_datetime,
    mentor,
    save_claim,
    save_mentor,
)


def test_dual_affiliation_and_multiple_current_emails_publish_primary_projection(tmp_path) -> None:
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
        source_url="https://cs.example.edu/faculty",
        profile_url="https://cs.example.edu/faculty/mentor",
    )
    second = claim(
        claim_id="claim_fixture_2002",
        mentor_id="mentor_fixture_0001",
        user_id=2002,
        login="fixture-two",
        issue_number=2,
        name="示例导师",
        email="mentor@sample.edu",
        organization_id="org_sample_ai",
        source_url="https://ai.sample.edu/faculty/mentor",
        profile_url="https://ai.sample.edu/faculty/mentor",
    )
    save_claim(root, first)
    save_claim(root, second)
    save_mentor(
        root,
        mentor(
            claim_ids=["claim_fixture_1001", "claim_fixture_2002"],
            dual=True,
        ),
    )

    data = load_repository(root)
    assert len(data.mentors) == 1
    latest = build_dataset(root, tmp_path / "dist", generated_at=fixed_datetime())
    catalog_path = tmp_path / "dist" / latest["catalog_path"]
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["record_count"] == 1
    unit_path = catalog["universities"][0]["units"][0]["path"]
    shard = json.loads((catalog_path.parent / unit_path).read_text(encoding="utf-8"))
    record = shard["records"][0]
    assert record["email"] == "mentor@example.edu"
    assert record["profile_url"] == "https://cs.example.edu/faculty/mentor"
    assert record["source_url"] == "https://cs.example.edu/faculty"
    assert record["contacts"][0]["source_url"] == "https://cs.example.edu/faculty/mentor"
    assert len(record["contacts"]) == 2
    assert len(record["affiliations"]) == 2


def test_retired_mentor_is_removed_from_import_shards_and_listed_in_revocations(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    value = claim(
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
    save_claim(root, value)
    retired = mentor(status="retired")
    for affiliation in retired["affiliations"]:
        affiliation["status"] = "former"
        affiliation["is_primary"] = False
        affiliation["ended_at"] = "2026-08-03"
    for contact in retired["contacts"]:
        contact["status"] = "former"
        contact["is_primary"] = False
    for profile in retired["profiles"]:
        profile["status"] = "former"
    retired["status_reason"] = "官网标注退休"
    save_mentor(root, retired)

    latest = build_dataset(root, tmp_path / "dist", generated_at=fixed_datetime())
    dataset_root = (tmp_path / "dist" / latest["catalog_path"]).parent
    catalog = json.loads((dataset_root / "catalog.json").read_text(encoding="utf-8"))
    revocations = json.loads((dataset_root / "revocations.json").read_text(encoding="utf-8"))
    assert catalog["record_count"] == 0
    assert revocations["records"][0]["community_record_id"] == "mentor_fixture_0001"
    assert revocations["records"][0]["status"] == "retired"


def test_same_current_email_cannot_belong_to_two_mentor_entities(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    first_claim = claim(
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
    second_claim = claim(
        claim_id="claim_fixture_2002",
        mentor_id="mentor_fixture_0002",
        user_id=2002,
        login="fixture-two",
        issue_number=2,
        name="另一位导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/other",
    )
    save_claim(root, first_claim)
    save_claim(root, second_claim)
    save_mentor(root, mentor())
    second_mentor = mentor(mentor_id="mentor_fixture_0002", claim_ids=["claim_fixture_2002"])
    second_mentor["names"][0]["value"] = "另一位导师"
    second_mentor["contacts"][0]["source_url"] = "https://cs.example.edu/faculty/other"
    second_mentor["affiliations"][0]["source_url"] = "https://cs.example.edu/faculty/other"
    second_mentor["profiles"][0]["url"] = "https://cs.example.edu/faculty/other"
    second_mentor["profiles"][0]["observed_at"] = "2026-08-03T00:00:00Z"
    save_mentor(root, second_mentor)

    with pytest.raises(RepositoryValidationError, match="邮箱 mentor@example.edu"):
        load_repository(root)


def test_organization_alias_matching_is_scoped_to_parent(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    data = load_repository(root)
    university = data.registry.match("示大", parent_id=None)
    school = data.registry.match("计算机系", parent_id="org_example_university")
    wrong_parent = data.registry.match("计算机系", parent_id="org_sample_university")
    assert university.organization_id == "org_example_university"
    assert school.organization_id == "org_example_cs"
    assert wrong_parent.status == "unknown"


def test_dataset_version_directory_is_immutable(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    output = tmp_path / "dist"
    build_dataset(root, output, generated_at=fixed_datetime())
    with pytest.raises(FileExistsError, match="版本已经存在"):
        build_dataset(root, output, generated_at=fixed_datetime())


def test_build_publishes_machine_readable_cc_by_4_metadata_and_page_notice(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    output = tmp_path / "dist"

    build_dataset(root, output, generated_at=fixed_datetime())

    license_document = json.loads((output / "license.json").read_text(encoding="utf-8"))
    assert license_document == {
        "schema_version": 1,
        "spdx_id": "CC-BY-4.0",
        "name": "Creative Commons Attribution 4.0 International",
        "url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "AutoEmailSender MentorData contributors",
    }
    page = (output / "index.html").read_text(encoding="utf-8")
    assert 'rel="license"' in page
    assert "CC BY 4.0" in page


def test_repository_rejects_a_different_or_missing_data_license(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    policy_path = root / "registry" / "policy.yml"
    policy = load_yaml(policy_path)
    policy["data_license"]["spdx_id"] = "ODC-By-1.0"
    write_yaml_atomic(policy_path, policy)

    with pytest.raises(RepositoryValidationError, match="CC-BY-4.0"):
        load_repository(root)
