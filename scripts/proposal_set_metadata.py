from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--review")
    args = parser.parse_args()
    paths = sorted(Path(args.directory).rglob("*.json"))
    proposals = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    invalid_row_count = 0
    if args.review:
        review = json.loads(Path(args.review).read_text(encoding="utf-8"))
        invalid_rows = review.get("invalid_rows")
        if not isinstance(invalid_rows, list):
            raise RuntimeError("organization review manifest has invalid_rows")
        invalid_row_count = len(invalid_rows)
    elif not paths:
        raise RuntimeError("proposal set is empty")
    all_auto_eligible = (
        bool(proposals)
        and invalid_row_count == 0
        and all(value.get("auto_eligible") is True for value in proposals)
    )
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is unavailable")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"all_auto_eligible={'true' if all_auto_eligible else 'false'}\n")
        handle.write(f"proposal_count={len(proposals)}\n")
        handle.write(f"invalid_row_count={invalid_row_count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
