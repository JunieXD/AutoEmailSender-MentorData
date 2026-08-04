from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mentor_data.errors import SubmissionError
from mentor_data.internal_pulls import (
    load_internal_pull,
    materialize_proposal_paths,
    proposal_paths_from_diff,
)


def _pull(*, branch: str = "batch/issue-40", draft: bool = True):
    return {
        "number": 88,
        "state": "open",
        "merged": False,
        "draft": draft,
        "html_url": "https://github.com/example/repository/pull/88",
        "title": "[批量投稿] 示例大学",
        "head": {
            "ref": branch,
            "sha": "a" * 40,
            "repo": {"full_name": "example/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
        "labels": [{"name": "status:manual-review"}],
    }


def test_internal_pull_requires_stable_internal_branch_and_one_status_label() -> None:
    pull = load_internal_pull(_pull(), expected_repository="example/repository")

    assert pull.kind == "batch"
    assert pull.issue_number == 40

    legacy = _pull(branch="batch/issue-40-123")
    with pytest.raises(SubmissionError, match="稳定"):
        load_internal_pull(legacy, expected_repository="example/repository")
    multiple = _pull()
    multiple["labels"].append({"name": "status:auto-eligible"})
    with pytest.raises(SubmissionError, match="只能有一个"):
        load_internal_pull(multiple, expected_repository="example/repository")


def test_diff_rejects_changes_outside_the_issue_proposal_paths(tmp_path: Path) -> None:
    pull = load_internal_pull(_pull(), expected_repository="example/repository")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "A\tproposals/batch-issue-40/issue-40-row-0001.json\n"
                "A\treviews/pending/batch-issue-40.json\n"
                "M\tmentor_data/cli.py\n"
            ),
        )

    with pytest.raises(SubmissionError, match="不允许"):
        proposal_paths_from_diff(tmp_path, pull, runner=runner)


def test_all_invalid_batch_accepts_a_review_manifest_without_proposals(tmp_path: Path) -> None:
    pull = load_internal_pull(_pull(), expected_repository="example/repository")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="A\treviews/pending/batch-issue-40.json\n",
        )

    assert proposal_paths_from_diff(tmp_path, pull, runner=runner) == [
        "reviews/pending/batch-issue-40.json"
    ]


def test_materialization_uses_git_without_a_shell_and_writes_only_fixed_paths(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b'{"safe":true}\n')

    destination = tmp_path / "candidate"
    materialize_proposal_paths(
        tmp_path,
        destination,
        source_sha="a" * 40,
        paths=["proposals/issue-40.json"],
        runner=runner,
    )

    assert (destination / "proposals" / "issue-40.json").read_bytes() == b'{"safe":true}\n'
    assert calls[0][0][-1] == f"{'a' * 40}:proposals/issue-40.json"
    assert calls[0][1] == {"check": True, "stdout": subprocess.PIPE}
    assert "shell" not in calls[0][1]
