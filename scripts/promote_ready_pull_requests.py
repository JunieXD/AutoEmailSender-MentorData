from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from mentor_data.promotion import PromotionQueue, github_output_path, write_github_outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--include-attention", action="store_true")
    parser.add_argument("--pr", type=int, help="仅处理指定 PR（仍通过可信队列）")
    parser.add_argument("--prs", help="按顺序处理逗号分隔的 PR 白名单")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        pull_numbers = None
        if args.prs:
            raw = args.prs.split(",")
            if any(not item.isascii() or not item.isdecimal() for item in raw):
                raise ValueError("--prs 必须是逗号分隔的正整数")
            pull_numbers = tuple(int(item) for item in raw)
            if any(item <= 0 for item in pull_numbers) or len(pull_numbers) != len(
                set(pull_numbers)
            ):
                raise ValueError("--prs 必须是无重复的正整数")
        if args.pr is not None and pull_numbers is not None:
            raise ValueError("--pr 不能与 --prs 同时使用")
        summary = PromotionQueue(
            root=Path(args.root),
            repository=args.repository,
            include_attention=args.include_attention,
            pull_number=args.pr,
            pull_numbers=pull_numbers,
            max_attempts=args.max_attempts,
        ).run()
        output_path = github_output_path(args.github_output)
        if output_path is not None:
            write_github_outputs(output_path, summary)
        print(
            f"scanned={summary.scanned} merged={summary.merged} "
            f"failed={summary.failed} skipped={summary.skipped} "
            f"retryable={summary.retryable}"
        )
        print(json.dumps(summary.results, ensure_ascii=False, separators=(",", ":")))
        return 1 if summary.failed else 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"ERROR: {str(error).splitlines()[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
