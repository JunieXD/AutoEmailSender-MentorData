from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from mentor_data.io_utils import iter_json_files, load_json


def parse_issue_numbers(value: str) -> list[int]:
    raw = value.split(",")
    if not raw or any(not item.isascii() or not item.isdecimal() for item in raw):
        raise ValueError("--issues must be a comma-separated list of positive integers")
    numbers = [int(item) for item in raw]
    if any(item <= 0 for item in numbers) or len(numbers) != len(set(numbers)):
        raise ValueError("--issues must contain unique positive integers")
    return numbers


def _receipt_issue_number(
    path: Path,
    *,
    validator: Draft202012Validator,
) -> int:
    receipt: Any = load_json(path)
    errors = sorted(validator.iter_errors(receipt), key=lambda item: list(item.path))
    if errors:
        raise ValueError(f"invalid promotion receipt {path}: {errors[0].message}")
    issue_number = receipt.get("issue_number") if isinstance(receipt, dict) else None
    if not isinstance(issue_number, int) or path.name != f"issue-{issue_number}.json":
        raise ValueError(f"promotion receipt filename does not match its Issue: {path}")
    return issue_number


def finalized_issue_numbers(
    root: Path,
    requested_issue_numbers: list[int] | None = None,
) -> list[int]:
    root = root.resolve()
    validator = Draft202012Validator(
        load_json(root / "schemas" / "promotion-receipt.schema.json"),
        format_checker=FormatChecker(),
    )
    receipt_root = root / "reviews" / "promotions"
    if requested_issue_numbers is None:
        numbers = [
            _receipt_issue_number(path, validator=validator)
            for path in iter_json_files(receipt_root)
        ]
        if len(numbers) != len(set(numbers)):
            raise ValueError("promotion receipts contain duplicate Issue numbers")
        return sorted(numbers)

    numbers: list[int] = []
    for issue_number in requested_issue_numbers:
        path = receipt_root / f"issue-{issue_number}.json"
        if not path.is_file():
            raise ValueError(f"promotion receipt is missing for Issue #{issue_number}")
        actual = _receipt_issue_number(path, validator=validator)
        if actual != issue_number:
            raise ValueError(f"promotion receipt does not match Issue #{issue_number}")
        numbers.append(actual)
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--issues",
        help=(
            "comma-separated Issue allowlist for one publication batch; "
            "omit only for scheduled full reconciliation"
        ),
    )
    args = parser.parse_args()
    requested = parse_issue_numbers(args.issues) if args.issues is not None else None
    numbers = finalized_issue_numbers(Path(args.root), requested)
    Path(args.output).write_text(
        "".join(f"{number}\n" for number in numbers),
        encoding="utf-8",
    )
    print(f"finalized_issue_count={len(numbers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
