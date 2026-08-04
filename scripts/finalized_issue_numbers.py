from __future__ import annotations

import argparse
from pathlib import Path

from mentor_data.repository import load_repository


def finalized_issue_numbers(root: Path) -> list[int]:
    data = load_repository(root, validate=True)
    return sorted(
        {
            receipt["issue_number"]
            for receipt in data.promotion_receipts
            if isinstance(receipt.get("issue_number"), int)
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    numbers = finalized_issue_numbers(Path(args.root))
    Path(args.output).write_text(
        "".join(f"{number}\n" for number in numbers),
        encoding="utf-8",
    )
    print(f"finalized_issue_count={len(numbers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
