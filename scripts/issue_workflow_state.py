from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mentor_data.errors import SubmissionError
from mentor_data.github_events import load_issue_event
from mentor_data.io_utils import load_yaml
from mentor_data.workflow_state import inspect_issue_workflow_state


def _write_outputs(path: Path, values: dict[str, str | int | bool | None]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, raw_value in values.items():
            if isinstance(raw_value, bool):
                value = "true" if raw_value else "false"
            elif raw_value is None:
                value = ""
            else:
                value = str(raw_value)
            if "\n" in value or "\r" in value:
                raise ValueError(f"GitHub output {key} 包含换行")
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--event", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue-number", required=True, type=int)
    parser.add_argument("--kind", required=True, choices=["mentor", "batch", "report"])
    parser.add_argument("--github-output")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        policy = load_yaml(root / "registry" / "policy.yml")
        event = load_issue_event(
            Path(args.event),
            max_body_bytes=policy["limits"]["max_issue_body_bytes"],
        )
        state = inspect_issue_workflow_state(
            root=root,
            event=event,
            repository=args.repository,
            issue_number=args.issue_number,
            kind=args.kind,
        )
        output_path = args.github_output or os.environ.get("GITHUB_OUTPUT")
        if output_path:
            _write_outputs(
                Path(output_path),
                {
                    "outcome": state.outcome,
                    "process": state.should_process,
                    "branch": state.branch,
                    "pr_number": state.pull_number,
                    "pr_url": state.pull_url,
                },
            )
        print(
            f"outcome={state.outcome} branch={state.branch} "
            f"pull={state.pull_number or '-'}"
        )
        return 0
    except (OSError, RuntimeError, SubmissionError, ValueError) as error:
        print(f"ERROR: {str(error).splitlines()[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

