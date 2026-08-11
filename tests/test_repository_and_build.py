from __future__ import annotations

import json

import pytest

from mentor_data.builder import build_dataset, stage_current_dataset
from mentor_data.errors import RepositoryValidationError
from mentor_data.io_utils import load_yaml, write_json_atomic, write_yaml_atomic
from mentor_data.repository import load_repository

from .helpers import (
    PROJECT_ROOT,
    build_test_repository,
    claim,
    fixed_datetime,
    mentor,
    save_claim,
    save_mentor,
)


def test_pr33_school_hierarchy_repair_has_no_active_reference_to_merged_department() -> None:
    data = load_repository(PROJECT_ROOT)
    former_department_id = "org_auto_6c245c14deeaa59981a5"
    school_id = "org_auto_398616f04bdab861ee7d"

    former_department = data.registry.by_id[former_department_id]
    school = data.registry.by_id[school_id]
    assert former_department["status"] == "merged"
    assert former_department["successor_id"] == school_id
    assert school["type"] == "school"
    assert school["parent_id"] == "org_auto_c8be69f24d66d48113c9"
    assert all(
        affiliation["organization_id"] != former_department_id
        for mentor_record in data.mentors
        for affiliation in mentor_record.get("affiliations", [])
        if affiliation.get("status") == "current"
    )
    assert all(
        claim_record.get("accepted", {}).get("organization_id") != former_department_id
        for claim_record in data.claims
        if claim_record.get("status") == "accepted"
    )

    resolution = next(
        item
        for item in data.organization_review_resolutions
        if item["id"] == "organization_review_issue_32"
    )
    correction = next(
        item
        for item in resolution["path_corrections"]
        if item["submitted"]["department"] == "集成电路学院(微电子学院)"
    )
    assert correction["target_organization_id"] == school_id


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
    shard = json.loads((tmp_path / "dist" / unit_path).read_text(encoding="utf-8"))
    record = shard["records"][0]
    assert record["email"] == "mentor@example.edu"
    assert record["profile_url"] == "https://cs.example.edu/faculty/mentor"
    assert record["source_url"] == "https://cs.example.edu/faculty"
    assert record["contacts"][0]["source_url"] == "https://cs.example.edu/faculty/mentor"
    assert len(record["contacts"]) == 2
    assert len(record["affiliations"]) == 2


def test_primary_affiliation_drives_title_profile_and_discovery_source(tmp_path) -> None:
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
        title="教授",
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
        source_url="https://ai.sample.edu/faculty",
        profile_url="https://ai.sample.edu/faculty/mentor",
        title="副教授",
    )
    save_claim(root, first)
    save_claim(root, second)
    value = mentor(claim_ids=[first["id"], second["id"]], dual=True)
    value["affiliations"][0]["is_primary"] = False
    value["affiliations"][1]["is_primary"] = True
    value["affiliations"][1]["title"] = "副教授"
    value["contacts"][0]["is_primary"] = False
    value["contacts"][1]["is_primary"] = True
    value["title"] = "教授"
    save_mentor(root, value)

    latest = build_dataset(root, tmp_path / "dist", generated_at=fixed_datetime())
    catalog = json.loads(
        (tmp_path / "dist" / latest["catalog_path"]).read_text(encoding="utf-8")
    )
    unit_path = catalog["universities"][0]["units"][0]["path"]
    record = json.loads(
        (tmp_path / "dist" / unit_path).read_text(encoding="utf-8")
    )["records"][0]

    assert record["school"] == "人工智能研究院"
    assert record["title"] == "副教授"
    assert record["profile_url"] == "https://ai.sample.edu/faculty/mentor"
    assert record["source_url"] == "https://ai.sample.edu/faculty"

    value["profiles"] = [
        item
        for item in value["profiles"]
        if item["affiliation_id"] != "aff_fixture_secondary"
    ]
    save_mentor(root, value)
    without_primary_profile = build_dataset(
        root,
        tmp_path / "dist-without-primary-profile",
        generated_at=fixed_datetime(),
    )
    without_primary_catalog = json.loads(
        (
            tmp_path
            / "dist-without-primary-profile"
            / without_primary_profile["catalog_path"]
        ).read_text(encoding="utf-8")
    )
    without_primary_unit = without_primary_catalog["universities"][0]["units"][0]
    without_primary_record = json.loads(
        (
            tmp_path
            / "dist-without-primary-profile"
            / without_primary_unit["path"]
        ).read_text(encoding="utf-8")
    )["records"][0]
    assert without_primary_record["profile_url"] is None


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


def test_active_mentor_becomes_stale_after_policy_deadline(tmp_path) -> None:
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
    active = mentor()
    active["last_verified_at"] = "2025-08-03T00:00:00Z"
    save_mentor(root, active)
    output = tmp_path / "dist"

    before_deadline = build_dataset(
        root,
        output,
        generated_at=fixed_datetime().replace(year=2026, day=2),
    )
    before_catalog = json.loads(
        (output / before_deadline["catalog_path"]).read_text(encoding="utf-8")
    )
    after_deadline = build_dataset(root, output, generated_at=fixed_datetime())
    after_catalog = json.loads(
        (output / after_deadline["catalog_path"]).read_text(encoding="utf-8")
    )
    revocations = json.loads(
        (
            output
            / after_deadline["catalog_path"].replace("catalog.json", "revocations.json")
        ).read_text(encoding="utf-8")
    )

    assert before_catalog["record_count"] == 1
    assert after_deadline["dataset_version"] != before_deadline["dataset_version"]
    assert after_catalog["record_count"] == 0
    assert revocations["records"][0]["status"] == "stale"
    assert revocations["records"][0]["observed_at"] == "2026-08-03T00:00:00Z"


def test_active_legacy_mentor_uses_updated_at_as_stale_baseline(tmp_path) -> None:
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
    active = mentor()
    active["last_verified_at"] = None
    active["updated_at"] = "2025-08-03T00:00:00Z"
    save_mentor(root, active)

    latest = build_dataset(root, tmp_path / "dist", generated_at=fixed_datetime())
    dataset_root = (tmp_path / "dist" / latest["catalog_path"]).parent
    catalog = json.loads((dataset_root / "catalog.json").read_text(encoding="utf-8"))
    revocations = json.loads(
        (dataset_root / "revocations.json").read_text(encoding="utf-8")
    )

    assert catalog["record_count"] == 0
    assert revocations["records"][0]["status"] == "stale"
    assert revocations["records"][0]["observed_at"] == "2026-08-03T00:00:00Z"


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


def test_unchanged_data_reuses_the_same_release(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    output = tmp_path / "dist"
    first = build_dataset(root, output, generated_at=fixed_datetime())
    second = build_dataset(root, output)

    assert second == first
    assert len(list((output / "releases").iterdir())) == 1


def test_rebuild_removes_deleted_static_files_but_keeps_release_archive(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    output = tmp_path / "dist"
    first = build_dataset(root, output, generated_at=fixed_datetime())
    archived_manifest = output / first["manifest_path"]
    archived_manifest_payload = archived_manifest.read_bytes()
    stale_file = output / "removed-page.html"
    stale_file.write_text("obsolete", encoding="utf-8")
    stale_directory = output / "removed-assets"
    stale_directory.mkdir()
    (stale_directory / "old.js").write_text("obsolete", encoding="utf-8")

    build_dataset(root, output)

    assert not stale_file.exists()
    assert not stale_directory.exists()
    assert archived_manifest.read_bytes() == archived_manifest_payload
    assert (output / "index.html").is_file()


def test_new_release_reuses_unchanged_content_addressed_shards(tmp_path) -> None:
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
    save_mentor(root, mentor())
    output = tmp_path / "archive"
    first = build_dataset(root, output, generated_at=fixed_datetime())
    first_catalog = json.loads((output / first["catalog_path"]).read_text(encoding="utf-8"))
    first_shard = first_catalog["universities"][0]["units"][0]["path"]

    policy_path = root / "registry" / "policy.yml"
    policy = load_yaml(policy_path)
    policy["minimum_app_version"] = "2.4.2"
    write_yaml_atomic(policy_path, policy)
    second = build_dataset(root, output)
    second_catalog = json.loads((output / second["catalog_path"]).read_text(encoding="utf-8"))

    assert second["dataset_version"] != first["dataset_version"]
    assert second_catalog["universities"][0]["units"][0]["path"] == first_shard
    assert len(list((output / "objects" / "sha256").glob("*.json"))) == 1


def test_staged_pages_artifact_contains_only_the_current_release(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    archive = tmp_path / "archive"
    first = build_dataset(root, archive, generated_at=fixed_datetime())
    policy_path = root / "registry" / "policy.yml"
    policy = load_yaml(policy_path)
    policy["minimum_app_version"] = "2.4.2"
    write_yaml_atomic(policy_path, policy)
    second = build_dataset(root, archive)

    output = tmp_path / "deploy"
    staged = stage_current_dataset(archive, output)

    assert staged == second
    assert (output / second["manifest_path"]).is_file()
    assert not (output / first["manifest_path"]).exists()


def test_staging_rejects_corrupt_archive_before_replacing_existing_output(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    archive = tmp_path / "archive"
    latest = build_dataset(root, archive, generated_at=fixed_datetime())
    manifest = json.loads((archive / latest["manifest_path"]).read_text(encoding="utf-8"))
    published_path = manifest["files"][0]["path"]
    (archive / published_path).write_bytes(b"corrupt")
    output = tmp_path / "deploy"
    output.mkdir()
    marker = output / "keep-on-failure.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="校验失败"):
        stage_current_dataset(archive, output)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_staging_rejects_an_output_directory_that_contains_the_archive(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    archive = tmp_path / "archive"
    build_dataset(root, archive, generated_at=fixed_datetime())

    with pytest.raises(ValueError, match="互相包含"):
        stage_current_dataset(archive, tmp_path)


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


def _promotion_receipt(*, kind: str, issue_number: int, pull_number: int = 10) -> dict:
    return {
        "schema_version": 1,
        "kind": kind,
        "issue_number": issue_number,
        "pull_number": pull_number,
        "pull_url": f"https://github.com/example/repository/pull/{pull_number}",
        "base_sha": "a" * 40,
        "proposal_commit_sha": "b" * 40,
        "finalized_at": "2026-08-03T00:00:00Z",
    }


def _save_promotion_receipt(root, receipt: dict) -> None:
    write_json_atomic(
        root / "reviews" / "promotions" / f"issue-{receipt['issue_number']}.json",
        receipt,
    )


def test_repository_accepts_mentor_batch_and_report_promotion_receipts(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    mentor_claim = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=40,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
    )
    batch_claim = claim(
        claim_id="claim_fixture_2002",
        mentor_id="mentor_fixture_0002",
        user_id=2002,
        login="fixture-two",
        issue_number=41,
        name="另一位导师",
        email="other@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/other",
    )
    save_claim(root, mentor_claim)
    save_claim(root, batch_claim)
    save_mentor(root, mentor())
    second_mentor = mentor(mentor_id="mentor_fixture_0002", claim_ids=["claim_fixture_2002"])
    second_mentor["names"][0]["value"] = "另一位导师"
    second_mentor["contacts"][0].update(
        {
            "value": "other@example.edu",
            "normalized_value": "other@example.edu",
            "source_url": "https://cs.example.edu/faculty/other",
        }
    )
    second_mentor["affiliations"][0]["source_url"] = "https://cs.example.edu/faculty/other"
    second_mentor["profiles"][0]["url"] = "https://cs.example.edu/faculty/other"
    save_mentor(root, second_mentor)

    write_json_atomic(
        root / "reviews" / "resolutions" / "batch-issue-42.json",
        {
            "schema_version": 1,
            "id": "organization_review_issue_42",
            "issue": {
                "number": 42,
                "url": "https://github.com/example/repository/issues/42",
            },
            "pull_request_number": 12,
            "review_comment_id": 100,
            "reviewer": {
                "github_user_id": 999,
                "github_login": "maintainer",
                "author_association": "OWNER",
            },
            "manifest_sha256": "c" * 64,
            "decided_at": "2026-08-03T00:00:00Z",
            "created_organization_ids": [],
            "updated_organization_ids": [],
            "mapped_proposal_ids": [],
            "rejected_proposal_ids": ["proposal_issue_42_row_1"],
            "invalid_rows": [],
            "decisions": [],
        },
    )
    write_json_atomic(
        root / "reports" / "resolutions" / "resolution_report_43.json",
        {
            "schema_version": 1,
            "id": "resolution_report_43",
            "mentor_id": "mentor_fixture_0001",
            "report_issue": {
                "number": 43,
                "url": "https://github.com/example/repository/issues/43",
            },
            "reporter": {"github_user_id": 5005, "github_login": "reporter"},
            "decision": "rejected",
            "before": {},
            "proposed": {},
            "accepted": {},
            "evidence_urls": ["https://cs.example.edu/faculty/mentor"],
            "moderator": {"github_user_id": 999, "github_login": "maintainer"},
            "decided_at": "2026-08-03T00:00:00Z",
            "reason": "官网仍显示当前信息",
        },
    )
    for offset, (kind, issue_number) in enumerate(
        (("mentor", 40), ("batch", 41), ("batch", 42), ("report", 43)),
        start=10,
    ):
        _save_promotion_receipt(
            root,
            _promotion_receipt(kind=kind, issue_number=issue_number, pull_number=offset),
        )

    load_repository(root)


def test_repository_rejects_promotion_receipt_without_final_data(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    _save_promotion_receipt(
        root,
        _promotion_receipt(kind="mentor", issue_number=50),
    )

    with pytest.raises(RepositoryValidationError, match="没有对应的最终数据"):
        load_repository(root)


def test_repository_rejects_promotion_receipt_with_mismatched_pull_url(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    value = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=51,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, value)
    save_mentor(root, mentor())
    receipt = _promotion_receipt(kind="mentor", issue_number=51, pull_number=10)
    receipt["pull_url"] = "https://github.com/example/repository/pull/11"
    _save_promotion_receipt(root, receipt)

    with pytest.raises(RepositoryValidationError, match="PR URL 与编号不一致"):
        load_repository(root)


def test_repository_rejects_promotion_receipt_with_pending_proposal(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    value = claim(
        claim_id="claim_fixture_1001",
        mentor_id="mentor_fixture_0001",
        user_id=1001,
        login="fixture-one",
        issue_number=52,
        name="示例导师",
        email="mentor@example.edu",
        organization_id="org_example_cs",
        source_url="https://cs.example.edu/faculty/mentor",
    )
    save_claim(root, value)
    save_mentor(root, mentor())
    submitted = value["submitted"]
    pending = {
        "schema_version": 1,
        "id": "proposal_issue_52_row_1",
        "kind": "mentor_contribution",
        "issue": {
            "number": 52,
            "url": "https://github.com/example/repository/issues/52",
            "batch_row": 1,
        },
        "contributor": {
            "github_user_id": 1001,
            "github_login_at_submission": "fixture-one",
            "github_user_type": "User",
            "submitted_at": "2026-08-03T00:00:00Z",
            "account_created_at": "2020-01-01T00:00:00Z",
            "account_age_days_at_submission": 2400,
        },
        "submitted": submitted,
        "accepted": submitted,
        "target_mentor_id": "mentor_fixture_0001",
        "match_status": "matched_email",
        "review_reasons": ["manual_review"],
        "auto_eligible": False,
        "created_at": "2026-08-03T00:00:00Z",
    }
    write_json_atomic(root / "proposals" / "issue-52.json", pending)
    _save_promotion_receipt(
        root,
        _promotion_receipt(kind="mentor", issue_number=52),
    )

    with pytest.raises(RepositoryValidationError, match="仍有待审核提案"):
        load_repository(root)
