from __future__ import annotations

import os
import re
from pathlib import Path

BRANCH_PATTERN = re.compile(r"^(submission|batch|report)/issue-([1-9][0-9]*)-([1-9][0-9]*)$")


def main() -> int:
    branch = os.environ.get("HEAD_REF", "")
    match = BRANCH_PATTERN.fullmatch(branch)
    if match is None:
        raise SystemExit(f"unsupported workflow branch: {branch!r}")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is unavailable")
    kind = match.group(1)
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"kind={kind}\n")
        handle.write(f"issue_number={match.group(2)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
