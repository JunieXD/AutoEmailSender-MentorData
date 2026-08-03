from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

ISSUE_NUMBER_PATTERN = re.compile(r"^[1-9][0-9]*$")


def pending_paths(root: Path) -> list[Path]:
    candidates = [
        *(root / "proposals").rglob("*.json"),
        *(root / "reports" / "pending").rglob("*.json"),
    ]
    return sorted(path for path in candidates if path.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    pending = pending_paths(root)
    issue_number = os.environ.get("ISSUE_NUMBER", "").strip()
    if issue_number and ISSUE_NUMBER_PATTERN.fullmatch(issue_number) is None:
        raise RuntimeError("ISSUE_NUMBER must be a positive integer")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is unavailable")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"publish={'false' if pending else 'true'}\n")
        handle.write(f"pending_count={len(pending)}\n")
        handle.write(f"issue_number={issue_number}\n")

    print(
        json.dumps(
            {
                "publish": not pending,
                "pending_count": len(pending),
                "issue_number": issue_number or None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
