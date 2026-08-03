from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.parse_workflow_branch import main


def _outputs(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if line
    )


def test_batch_branch_uses_comment_reviewer_and_reports_pending_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_number = 30
    resolution_path = tmp_path / "reviews" / "resolutions" / f"batch-issue-{issue_number}.json"
    resolution_path.parent.mkdir(parents=True)
    resolution_path.write_text(
        json.dumps(
            {
                "id": f"organization_review_issue_{issue_number}",
                "issue": {"number": issue_number},
                "reviewer": {"github_user_id": 999},
            }
        ),
        encoding="utf-8",
    )
    proposal_path = (
        tmp_path
        / "proposals"
        / f"batch-issue-{issue_number}"
        / f"issue-{issue_number}-row-0001.json"
    )
    proposal_path.parent.mkdir(parents=True)
    proposal_path.write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "github-output.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HEAD_REF", f"batch/issue-{issue_number}-123")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    assert main() == 0

    assert _outputs(output_path) == {
        "kind": "batch",
        "issue_number": str(issue_number),
        "moderator_id": "999",
        "pending": "true",
    }

    proposal_path.unlink()
    output_path.unlink()
    assert main() == 0
    assert _outputs(output_path)["pending"] == "false"


def test_batch_branch_rejects_mismatched_review_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution_path = tmp_path / "reviews" / "resolutions" / "batch-issue-30.json"
    resolution_path.parent.mkdir(parents=True)
    resolution_path.write_text(
        json.dumps(
            {
                "id": "organization_review_issue_31",
                "issue": {"number": 31},
                "reviewer": {"github_user_id": 999},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HEAD_REF", "batch/issue-30-123")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output.txt"))

    with pytest.raises(RuntimeError, match="invalid reviewer metadata"):
        main()
