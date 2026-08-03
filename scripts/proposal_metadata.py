from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("proposal")
    args = parser.parse_args()
    value = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    auto_eligible = value.get("auto_eligible") is True
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is unavailable")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"auto_eligible={'true' if auto_eligible else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
