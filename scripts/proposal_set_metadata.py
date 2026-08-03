from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    paths = sorted(Path(args.directory).rglob("*.json"))
    if not paths:
        raise RuntimeError("proposal set is empty")
    proposals = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    all_auto_eligible = all(value.get("auto_eligible") is True for value in proposals)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is unavailable")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"all_auto_eligible={'true' if all_auto_eligible else 'false'}\n")
        handle.write(f"proposal_count={len(proposals)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
