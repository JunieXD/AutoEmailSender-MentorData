from __future__ import annotations

import pytest

from mentor_data.io_utils import write_json_atomic
from scripts.finalized_issue_numbers import finalized_issue_numbers, parse_issue_numbers

from .helpers import build_test_repository


def test_finalized_issue_numbers_come_from_durable_promotion_receipts(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    for issue_number in (42, 40):
        pull_number = issue_number + 100
        write_json_atomic(
            root / "reviews" / "resolutions" / f"batch-issue-{issue_number}.json",
            {
                "schema_version": 1,
                "id": f"organization_review_issue_{issue_number}",
                "issue": {
                    "number": issue_number,
                    "url": f"https://github.com/example/repository/issues/{issue_number}",
                },
                "pull_request_number": pull_number,
                "review_comment_id": issue_number + 1_000,
                "reviewer": {
                    "github_user_id": 999,
                    "github_login": "maintainer",
                    "author_association": "OWNER",
                },
                "manifest_sha256": "c" * 64,
                "decided_at": "2026-08-04T00:00:00Z",
                "created_organization_ids": [],
                "updated_organization_ids": [],
                "mapped_proposal_ids": [],
                "rejected_proposal_ids": [f"proposal_issue_{issue_number}_row_1"],
                "invalid_rows": [],
                "decisions": [],
            },
        )
        write_json_atomic(
            root / "reviews" / "promotions" / f"issue-{issue_number}.json",
            {
                "schema_version": 1,
                "kind": "batch",
                "issue_number": issue_number,
                "pull_number": pull_number,
                "pull_url": (
                    "https://github.com/example/repository/pull/"
                    f"{pull_number}"
                ),
                "base_sha": "a" * 40,
                "proposal_commit_sha": "b" * 40,
                "finalized_at": "2026-08-04T00:00:00Z",
            },
        )

    assert finalized_issue_numbers(root) == [40, 42]


def test_explicit_issue_allowlist_preserves_order_and_requires_every_receipt(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    for issue_number in (40, 42):
        write_json_atomic(
            root / "reviews" / "promotions" / f"issue-{issue_number}.json",
            {
                "schema_version": 1,
                "kind": "mentor",
                "issue_number": issue_number,
                "pull_number": issue_number + 100,
                "pull_url": (
                    "https://github.com/example/repository/pull/"
                    f"{issue_number + 100}"
                ),
                "base_sha": "a" * 40,
                "proposal_commit_sha": "b" * 40,
                "finalized_at": "2026-08-04T00:00:00Z",
            },
        )

    assert finalized_issue_numbers(root, [42, 40]) == [42, 40]
    with pytest.raises(ValueError, match="missing for Issue #41"):
        finalized_issue_numbers(root, [41])


@pytest.mark.parametrize("value", ["", "40,", "40,,42", "40,40", "0", "４０"])
def test_explicit_issue_allowlist_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_issue_numbers(value)
