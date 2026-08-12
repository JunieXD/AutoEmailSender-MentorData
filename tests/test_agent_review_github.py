from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mentor_data.agent_review_github import GitHubReviewClient

from .helpers import PROJECT_ROOT


def _pull_payload(number: int = 12) -> dict:
    return {
        "number": number,
        "html_url": f"https://github.com/example/repository/pull/{number}",
        "title": "[批量投稿] 示例大学计算机学院",
        "state": "open",
        "merged": False,
        "draft": True,
        "labels": [{"name": "status:manual-review"}],
        "head": {
            "ref": "batch/issue-11",
            "sha": "a" * 40,
            "repo": {"full_name": "example/repository"},
        },
        "base": {"ref": "main", "sha": "b" * 40},
    }


def test_queue_flattens_pages_and_returns_only_internal_batch_pulls() -> None:
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(command)
        report_pull = _pull_payload(13)
        report_pull["head"] = {
            **report_pull["head"],
            "ref": "report/issue-11",
        }
        payload = [[_pull_payload()], [report_pull]]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    client = GitHubReviewClient(
        repository="example/repository",
        root=PROJECT_ROOT,
        runner=runner,
    )

    pulls = client.list_open_batch_pulls()

    assert [item.number for item in pulls] == [12]
    assert commands == [
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/example/repository/pulls?state=open&per_page=100",
        ]
    ]


def test_submit_comment_uses_json_stdin_without_shell_interpolation(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"id": 99, "html_url": "https://github.test/comment/99"}),
            "",
        )

    client = GitHubReviewClient(
        repository="example/repository",
        root=tmp_path,
        runner=runner,
    )
    body = "<!-- marker -->\n`$danger`"

    result = client.submit_review_comment(12, body)

    assert result["id"] == 99
    command, input_value = calls[0]
    assert command[-2:] == ["--input", "-"]
    assert json.loads(input_value or "{}")["body"] == body
    assert body not in command


def test_fetch_main_organizations_returns_compact_active_options() -> None:
    registry = """\
schema_version: 1
organizations:
- id: org_example_university
  type: university
  canonical_name: 示例大学
  parent_id: null
  aliases: []
  official_urls: [https://example.edu/]
  approved_domains: [example.edu]
  status: active
  successor_id: null
- id: org_old_school
  type: school
  canonical_name: 旧学院
  parent_id: org_example_university
  aliases: []
  official_urls: []
  approved_domains: []
  status: merged
  successor_id: null
"""

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, registry, "")

    client = GitHubReviewClient(
        repository="example/repository",
        root=PROJECT_ROOT,
        runner=runner,
    )

    assert client.fetch_main_organizations() == [
        {
            "id": "org_example_university",
            "type": "university",
            "canonical_name": "示例大学",
            "parent_id": None,
            "aliases": [],
            "official_urls": ["https://example.edu/"],
            "approved_domains": ["example.edu"],
            "lineage_ids": ["org_example_university"],
            "lineage_names": ["示例大学"],
        }
    ]
