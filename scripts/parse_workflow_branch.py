from __future__ import annotations

import json
import os
import re
from pathlib import Path

BRANCH_PATTERN = re.compile(r"^(submission|batch|report)/issue-([1-9][0-9]*)-([1-9][0-9]*)$")


def _batch_moderator_id(issue_number: str) -> int:
    path = Path("reviews") / "resolutions" / f"batch-issue-{issue_number}.json"
    try:
        resolution = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("batch organization review resolution is unavailable") from error
    if not isinstance(resolution, dict):
        raise RuntimeError("batch organization review resolution has invalid reviewer metadata")
    issue = resolution.get("issue")
    reviewer = resolution.get("reviewer")
    moderator_id = reviewer.get("github_user_id") if isinstance(reviewer, dict) else None
    if (
        resolution.get("id") != f"organization_review_issue_{issue_number}"
        or not isinstance(issue, dict)
        or issue.get("number") != int(issue_number)
        or isinstance(moderator_id, bool)
        or not isinstance(moderator_id, int)
        or moderator_id <= 0
    ):
        raise RuntimeError("batch organization review resolution has invalid reviewer metadata")
    return moderator_id


def _has_pending_proposal(kind: str, issue_number: str) -> bool:
    if kind == "submission":
        return (Path("proposals") / f"issue-{issue_number}.json").is_file()
    if kind == "report":
        return (Path("reports") / "pending" / f"issue-{issue_number}.json").is_file()
    directory = Path("proposals") / f"batch-issue-{issue_number}"
    return directory.is_dir() and any(directory.glob("*.json"))


def main() -> int:
    branch = os.environ.get("HEAD_REF", "")
    match = BRANCH_PATTERN.fullmatch(branch)
    if match is None:
        raise SystemExit(f"unsupported workflow branch: {branch!r}")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is unavailable")
    kind = match.group(1)
    issue_number = match.group(2)
    moderator_id = _batch_moderator_id(issue_number) if kind == "batch" else ""
    pending = _has_pending_proposal(kind, issue_number)
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"kind={kind}\n")
        handle.write(f"issue_number={issue_number}\n")
        handle.write(f"moderator_id={moderator_id}\n")
        handle.write(f"pending={'true' if pending else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
