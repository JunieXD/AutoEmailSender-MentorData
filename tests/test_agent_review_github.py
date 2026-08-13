from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mentor_data.agent_review import AgentReviewError
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


def test_transient_github_eof_is_retried(tmp_path: Path) -> None:
    attempts = 0
    sleeps: list[float] = []

    def runner(command, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise subprocess.CalledProcessError(1, command, stderr="unexpected EOF")
        return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True}), "")

    client = GitHubReviewClient(
        repository="example/repository",
        root=tmp_path,
        runner=runner,
        sleeper=sleeps.append,
    )

    assert client._json("repos/example/repository") == {"ok": True}
    assert attempts == 2
    assert sleeps == [1.0]


def test_mutating_github_command_is_not_replayed_after_eof(tmp_path: Path) -> None:
    attempts = 0

    def runner(command, **kwargs):
        nonlocal attempts
        attempts += 1
        raise subprocess.CalledProcessError(1, command, stderr="unexpected EOF")

    client = GitHubReviewClient(
        repository="example/repository",
        root=tmp_path,
        runner=runner,
        sleeper=lambda seconds: None,
    )

    with pytest.raises(AgentReviewError):
        client.submit_review_comment(12, "review")

    assert attempts == 1


def test_retry_promotion_dispatches_only_the_trusted_workflow(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    client = GitHubReviewClient(
        repository="example/repository",
        root=tmp_path,
        runner=runner,
    )
    client._json = lambda endpoint: _pull_payload()  # type: ignore[method-assign]
    client.official_review_comments = (  # type: ignore[method-assign]
        lambda pull_number: [{"id": 99}]
    )

    result = client.retry_promotion(12)

    assert result["dispatched"] is True
    assert calls == [
        [
            "gh",
            "workflow",
            "run",
            "promote-ready-pulls.yml",
            "--repo",
            "example/repository",
            "--ref",
            "main",
            "-f",
            "pull_number=12",
        ]
    ]


def test_deferred_batch_comment_keeps_official_marker_and_dispatches_one_allowlist(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[:4] == ["gh", "api", "--method", "POST"]:
            body = json.loads(kwargs["input"])["body"]
            assert body.startswith(
                "<!-- mentor-data-organization-review:v1 -->\n"
                "<!-- mentor-data-batch-submit:v1 -->\n"
            )
            return subprocess.CompletedProcess(command, 0, json.dumps({"id": 99}), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = GitHubReviewClient(
        repository="example/repository",
        root=tmp_path,
        runner=runner,
    )
    body = "<!-- mentor-data-organization-review:v1 -->\n```json\n{}\n```"

    client.submit_review_comment(11, body, suppress_trigger=True)
    client.dispatch_promotion_queue([11, 12])

    assert calls[-1][-2:] == ["-f", "pull_numbers=11,12"]


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


def test_status_matches_named_promotion_workflow_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GitHubReviewClient(
        repository="example/repository",
        root=PROJECT_ROOT,
    )
    pull = {
        **_pull_payload(),
        "updated_at": "2026-08-12T10:00:00Z",
        "merged_at": None,
    }
    workflow_runs = {
        "workflow_runs": [
            {
                "id": 100,
                "display_title": "Promote ready mentor data after review PR #11",
                "event": "issue_comment",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-08-12T09:59:00Z",
                "updated_at": "2026-08-12T10:00:00Z",
                "html_url": "https://github.test/actions/runs/100",
                "pull_requests": [],
            },
            {
                "id": 101,
                "display_title": "Promote ready mentor data after review PR #12",
                "event": "issue_comment",
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-08-12T10:01:00Z",
                "updated_at": "2026-08-12T10:02:00Z",
                "html_url": "https://github.test/actions/runs/101",
                "pull_requests": [],
            },
        ]
    }

    def fake_json(endpoint: str):
        if endpoint.endswith("pulls/12"):
            return pull
        if endpoint.endswith("issues/11"):
            return {"state": "open"}
        if "actions/workflows/promote-ready-pulls.yml/runs" in endpoint:
            return workflow_runs
        if endpoint.endswith("actions/runs/101/jobs?per_page=100"):
            return {
                "jobs": [
                    {
                        "name": "promote",
                        "steps": [
                            {
                                "name": "Install dependencies",
                                "number": 1,
                                "status": "completed",
                                "conclusion": "success",
                                "started_at": "2026-08-12T10:01:05Z",
                                "completed_at": "2026-08-12T10:01:15Z",
                            },
                            {
                                "name": "Promote queue",
                                "number": 2,
                                "status": "in_progress",
                                "conclusion": None,
                                "started_at": "2026-08-12T10:01:16Z",
                                "completed_at": None,
                            },
                        ],
                    }
                ]
            }
        if endpoint.endswith("commits/" + "a" * 40 + "/check-runs"):
            return {"check_runs": []}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_json", fake_json)
    monkeypatch.setattr(
        client,
        "official_review_comments",
        lambda pull_number: [{"id": 99, "body": "official"}],
    )

    result = client.status(12)

    promotion_run = result["promotion_run"]
    assert promotion_run["id"] == 101
    assert promotion_run["attempt"] == 1
    assert promotion_run["retry_count"] == 0
    assert promotion_run["status"] == "in_progress"
    assert promotion_run["started_at"] == "2026-08-12T10:01:00Z"
    assert promotion_run["completed_at"] is None
    assert promotion_run["duration_seconds"] is None
    assert promotion_run["latest_stage"]["step"] == "Promote queue"
    assert promotion_run["stage_seconds"] == [
        {"job": "promote", "step": "Install dependencies", "seconds": 10.0}
    ]


def test_status_matches_batch_allowlist_workflow_run(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubReviewClient(repository="example/repository", root=PROJECT_ROOT)
    pull = {
        **_pull_payload(),
        "updated_at": "2026-08-12T10:00:00Z",
        "merged_at": None,
    }

    def fake_json(endpoint: str):
        if endpoint.endswith("pulls/12"):
            return pull
        if endpoint.endswith("issues/11"):
            return {"state": "open"}
        if "actions/workflows/promote-ready-pulls.yml/runs" in endpoint:
            return {
                "workflow_runs": [
                    {
                        "id": 102,
                        "display_title": "Promote ready mentor data batch PRs 11,12,13",
                        "event": "workflow_dispatch",
                        "status": "in_progress",
                        "conclusion": None,
                        "created_at": "2026-08-12T10:01:00Z",
                        "updated_at": "2026-08-12T10:02:00Z",
                        "html_url": "https://github.test/actions/runs/102",
                        "pull_requests": [],
                    }
                ]
            }
        if endpoint.endswith("actions/runs/102/jobs?per_page=100"):
            return {
                "jobs": [
                    {
                        "name": "promote",
                        "status": "in_progress",
                        "conclusion": None,
                        "started_at": "2026-08-12T10:01:00Z",
                        "completed_at": None,
                        "steps": [],
                    }
                ]
            }
        if endpoint.endswith("commits/" + "a" * 40 + "/check-runs"):
            return {"check_runs": []}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_json", fake_json)
    monkeypatch.setattr(client, "official_review_comments", lambda pull_number: [{"id": 99}])

    result = client.status(12)

    assert result["promotion_run"]["id"] == 102


def test_status_keeps_existing_fallback_when_actions_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GitHubReviewClient(
        repository="example/repository",
        root=PROJECT_ROOT,
    )
    pull = {
        **_pull_payload(),
        "updated_at": "2026-08-12T10:00:00Z",
        "merged_at": None,
    }

    def fake_json(endpoint: str):
        if endpoint.endswith("pulls/12"):
            return pull
        if endpoint.endswith("issues/11"):
            return {"state": "open"}
        if "actions/workflows/promote-ready-pulls.yml/runs" in endpoint:
            raise AgentReviewError("review_github_failed", "Actions 不可用")
        if endpoint.endswith("commits/" + "a" * 40 + "/check-runs"):
            return {"check_runs": []}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_json", fake_json)
    monkeypatch.setattr(
        client,
        "official_review_comments",
        lambda pull_number: [{"id": 99, "body": "official"}],
    )

    result = client.status(12)

    assert result["promotion_run"] is None
    assert result["review_comments"] == 1


def test_status_many_reuses_workflow_lists_and_shared_run_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GitHubReviewClient(repository="example/repository", root=PROJECT_ROOT)
    calls: list[str] = []

    def fake_json(endpoint: str):
        calls.append(endpoint)
        if endpoint.endswith("pulls/12"):
            return {
                **_pull_payload(12),
                "updated_at": "2026-08-12T10:00:00Z",
                "merged_at": None,
            }
        if endpoint.endswith("pulls/13"):
            pull = _pull_payload(13)
            pull["head"] = {**pull["head"], "ref": "batch/issue-12", "sha": "b" * 40}
            return {**pull, "updated_at": "2026-08-12T10:00:00Z", "merged_at": None}
        if endpoint.endswith("issues/11") or endpoint.endswith("issues/12"):
            return {"state": "open"}
        if endpoint.endswith("actions/workflows/promote-ready-pulls.yml/runs?per_page=50"):
            return {
                "workflow_runs": [
                    {
                        "id": 200,
                        "run_number": 25,
                        "run_attempt": 2,
                        "display_title": "Promote ready mentor data batch PRs 12,13",
                        "event": "workflow_dispatch",
                        "status": "in_progress",
                        "conclusion": None,
                        "created_at": "2026-08-12T10:01:00Z",
                        "run_started_at": "2026-08-12T10:01:05Z",
                        "updated_at": "2026-08-12T10:02:00Z",
                        "html_url": "https://github.test/actions/runs/200",
                        "pull_requests": [],
                    }
                ]
            }
        if endpoint.endswith("actions/workflows/pages.yml/runs?per_page=50"):
            return {"workflow_runs": []}
        if endpoint.endswith("actions/runs/200/jobs?per_page=100"):
            return {
                "jobs": [
                    {
                        "name": "promote",
                        "steps": [
                            {
                                "name": "Promote queue",
                                "number": 4,
                                "status": "in_progress",
                                "conclusion": None,
                                "started_at": "2026-08-12T10:01:20Z",
                                "completed_at": None,
                            }
                        ],
                    }
                ]
            }
        if endpoint.endswith("commits/" + "a" * 40 + "/check-runs") or endpoint.endswith(
            "commits/" + "b" * 40 + "/check-runs"
        ):
            return {"check_runs": []}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_json", fake_json)
    monkeypatch.setattr(client, "official_review_comments", lambda pull_number: [{"id": 99}])

    results = client.status_many([12, 13], issue_numbers={12: 11, 13: 12})

    assert [item["promotion_run"]["id"] for item in results] == [200, 200]
    assert results[0]["promotion_run"]["attempt"] == 2
    assert results[0]["promotion_run"]["retry_count"] == 1
    assert results[0]["promotion_run"]["latest_stage"]["step"] == "Promote queue"
    assert calls.count(
        "repos/example/repository/actions/workflows/promote-ready-pulls.yml/runs?per_page=50"
    ) == 1
    assert calls.count(
        "repos/example/repository/actions/workflows/pages.yml/runs?per_page=50"
    ) == 1
    assert calls.count("repos/example/repository/actions/runs/200/jobs?per_page=100") == 1


def test_status_tracks_publication_run_for_merged_pull_with_open_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GitHubReviewClient(repository="example/repository", root=PROJECT_ROOT)
    pull = {
        **_pull_payload(12),
        "state": "closed",
        "merged": True,
        "merged_at": "2026-08-12T10:02:00Z",
        "updated_at": "2026-08-12T10:02:00Z",
    }

    def fake_json(endpoint: str):
        if endpoint.endswith("pulls/12"):
            return pull
        if endpoint.endswith("issues/11"):
            return {"state": "open"}
        if endpoint.endswith("actions/workflows/promote-ready-pulls.yml/runs?per_page=50"):
            return {"workflow_runs": []}
        if endpoint.endswith("actions/workflows/pages.yml/runs?per_page=50"):
            return {
                "workflow_runs": [
                    {
                        "id": 300,
                        "run_number": 30,
                        "run_attempt": 1,
                        "display_title": "Publish MentorData for Issues 10,11,12",
                        "event": "workflow_dispatch",
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": "2026-08-12T10:03:00Z",
                        "run_started_at": "2026-08-12T10:03:05Z",
                        "updated_at": "2026-08-12T10:04:05Z",
                        "html_url": "https://github.test/actions/runs/300",
                    }
                ]
            }
        if endpoint.endswith("actions/runs/300/jobs?per_page=100"):
            return {
                "jobs": [
                    {
                        "name": "deploy",
                        "steps": [
                            {
                                "name": "Deploy Pages",
                                "number": 3,
                                "status": "completed",
                                "conclusion": "success",
                                "started_at": "2026-08-12T10:03:35Z",
                                "completed_at": "2026-08-12T10:04:00Z",
                            }
                        ],
                    }
                ]
            }
        if endpoint.endswith("commits/" + "a" * 40 + "/check-runs"):
            return {"check_runs": []}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_json", fake_json)
    monkeypatch.setattr(client, "official_review_comments", lambda pull_number: [{"id": 99}])

    result = client.status(12)

    assert result["publication_run"]["id"] == 300
    assert result["publication_run"]["duration_seconds"] == 55.0
    assert result["publication_run"]["stage_seconds"] == [
        {"job": "deploy", "step": "Deploy Pages", "seconds": 25.0}
    ]


def test_status_keeps_completed_publication_run_after_issue_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GitHubReviewClient(repository="example/repository", root=PROJECT_ROOT)
    pull = {
        **_pull_payload(12),
        "state": "closed",
        "merged": True,
        "merged_at": "2026-08-12T10:02:00Z",
        "updated_at": "2026-08-12T10:02:00Z",
    }

    def fake_json(endpoint: str):
        if endpoint.endswith("pulls/12"):
            return pull
        if endpoint.endswith("issues/11"):
            return {"state": "closed"}
        if endpoint.endswith("actions/workflows/promote-ready-pulls.yml/runs?per_page=50"):
            return {"workflow_runs": []}
        if endpoint.endswith("actions/workflows/pages.yml/runs?per_page=50"):
            return {
                "workflow_runs": [
                    {
                        "id": 300,
                        "display_title": "Publish MentorData for Issues 11",
                        "event": "workflow_dispatch",
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": "2026-08-12T10:03:00Z",
                        "updated_at": "2026-08-12T10:04:05Z",
                        "html_url": "https://github.test/actions/runs/300",
                    }
                ]
            }
        if endpoint.endswith("actions/runs/300/jobs?per_page=100"):
            return {"jobs": []}
        if endpoint.endswith("commits/" + "a" * 40 + "/check-runs"):
            return {"check_runs": []}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_json", fake_json)
    monkeypatch.setattr(client, "official_review_comments", lambda pull_number: [{"id": 99}])

    result = client.status(12)

    assert result["issue_state"] == "closed"
    assert result["publication_run"]["id"] == 300
