from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pytest

from mentor_data.agent_review import (
    AgentReviewError,
    PullSnapshot,
    cache_snapshot,
    load_draft,
    save_draft,
)
from mentor_data.agent_review_cli import (
    _answer_many,
    _brief,
    _check,
    _classify_status,
    _discover_root,
    _doctor,
    _questions,
    _queue,
    _root,
    _status,
)


def _make_repository(root: Path) -> None:
    (root / "schemas").mkdir(parents=True)
    (root / "registry").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "schemas" / "organization-review.schema.json").write_text("{}", encoding="utf-8")
    (root / "registry" / "organizations.yml").write_text("organizations: []\n", encoding="utf-8")


def test_discovers_repository_from_working_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "mentor-data"
    nested = root / "nested" / "work"
    nested.mkdir(parents=True)
    _make_repository(root)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("MENTOR_DATA_ROOT", raising=False)

    assert _discover_root() == (root, "working-tree")


def test_environment_root_must_be_complete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MENTOR_DATA_ROOT", str(tmp_path))
    args = argparse.Namespace(root=None)

    with pytest.raises(AgentReviewError) as captured:
        _root(args)

    assert captured.value.code == "review_root_invalid"


def test_doctor_reports_machine_readable_setup_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "mentor-data"
    _make_repository(root)
    monkeypatch.setattr("mentor_data.agent_review_cli.shutil.which", lambda name: None)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    result = _doctor(argparse.Namespace(root=str(root)))

    assert result["ready"] is False
    assert result["checks"] == {
        "trusted_root": True,
        "cli_on_path": False,
        "github_cli_on_path": False,
        "codex_skill_available": False,
    }
    assert result["actions"][0] == f"uv tool install --editable {root}"
    json.dumps(result)


def test_doctor_does_not_treat_project_virtualenv_as_global_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "mentor-data"
    _make_repository(root)
    project_cli = root / ".venv" / "bin" / "mentor-data"
    project_cli.parent.mkdir(parents=True)
    project_cli.touch()
    monkeypatch.setattr(
        "mentor_data.agent_review_cli.shutil.which",
        lambda name: str(project_cli) if name == "mentor-data" else "/usr/bin/gh",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    result = _doctor(argparse.Namespace(root=str(root)))

    assert result["checks"]["cli_on_path"] is False
    assert result["actions"][0] == f"uv tool install --editable {root}"


def test_doctor_accepts_repository_skill_without_global_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "mentor-data"
    _make_repository(root)
    repository_skill = root / ".agents" / "skills" / "review-mentor-data-pr"
    repository_skill.mkdir(parents=True)
    (repository_skill / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        "mentor_data.agent_review_cli.shutil.which",
        lambda name: f"/usr/local/bin/{name}",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    result = _doctor(argparse.Namespace(root=str(root)))

    assert result["ready"] is True
    assert result["checks"]["codex_skill_available"] is True
    assert result["skill"]["source"] == "repository"
    assert result["actions"] == []


def _answer_many_args(**overrides) -> argparse.Namespace:
    values = {
        "repository": "example/repository",
        "pr": 88,
        "ids": None,
        "type": "ambiguous_organization",
        "level": None,
        "query": None,
        "choice": "map-existing",
        "confirm_count": 2,
        "organization_id": "org_example_cs",
        "organization_type": None,
        "canonical_name": None,
        "official_url": None,
        "approved_domain": [],
        "reason": "用户确认路径归属",
        "former_affiliation_id": None,
        "make_primary": False,
        "save_path_correction": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _answer_many_draft(pull: PullSnapshot, digest: str) -> dict:
    questions = [
        {
            "id": f"q_{index}",
            "group_id": f"group_{index}",
            "type": "ambiguous_organization",
            "level": "department",
            "path": f"示例大学 / 计算机学院 / 待定系{index}",
            "prompt": "请选择机构",
            "reason": "无法自动判断",
            "rule_default": None,
            "context_recommendation": None,
            "recommendation_confidence": None,
            "path_correction_scopes": ["current-batch", "future-identical-path"],
            "path_correction_choices": ["map-existing"],
            "options": [
                {
                    "value": "map-existing",
                    "label": "映射现有机构",
                    "requires": ["organization_id"],
                }
            ],
            "context": {},
            "status": "pending",
            "answer": None,
        }
        for index in (1, 2)
    ]
    return {
        "schema_version": 1,
        "kind": "agent_organization_review_draft",
        "repository": "example/repository",
        "pull": pull.as_dict(),
        "manifest_sha256": digest,
        "answers": {},
        "questions": questions,
    }


def test_answer_many_filters_and_writes_all_answers_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    pull = PullSnapshot(
        number=88,
        issue_number=87,
        title="批量审核",
        url="https://github.test/pull/88",
        branch="batch/issue-87",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )
    digest = "c" * 64
    original = _answer_many_draft(pull, digest)
    save_draft(workspace, original)
    calls = {"refresh": 0, "main": 0, "plan": 0}

    class Client:
        def fetch_main_organizations(self):
            calls["main"] += 1
            return []

    def refresh(client, selected_workspace, pull_number):
        calls["refresh"] += 1
        assert selected_workspace == workspace
        assert pull_number == 88
        return pull, {"kind": "batch_organization_review"}, digest

    def replan(**kwargs):
        calls["plan"] += 1
        assert set(kwargs["previous_answers"]) == {"q_1", "q_2"}
        result = copy.deepcopy(original)
        result["answers"] = copy.deepcopy(kwargs["previous_answers"])
        for question in result["questions"]:
            question["status"] = "answered"
            question["answer"] = copy.deepcopy(result["answers"][question["id"]])
        return result

    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: Client())
    monkeypatch.setattr("mentor_data.agent_review_cli._refresh", refresh)
    monkeypatch.setattr("mentor_data.agent_review_cli.plan_review", replan)

    result = _answer_many(_answer_many_args(), tmp_path, workspace)

    assert result["answered_count"] == 2
    assert result["path_correction_scope"] == "future-identical-path"
    assert result["pending_questions"] == 0
    assert calls == {"refresh": 1, "main": 1, "plan": 1}
    assert set(load_draft(workspace, 88)["answers"]) == {"q_1", "q_2"}


def test_answer_many_count_mismatch_leaves_draft_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    pull = PullSnapshot(
        number=88,
        issue_number=87,
        title="批量审核",
        url="https://github.test/pull/88",
        branch="batch/issue-87",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )
    digest = "c" * 64
    original = _answer_many_draft(pull, digest)
    save_draft(workspace, original)

    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: object())
    monkeypatch.setattr(
        "mentor_data.agent_review_cli._refresh",
        lambda client, selected_workspace, pull_number: (
            pull,
            {"kind": "batch_organization_review"},
            digest,
        ),
    )

    with pytest.raises(AgentReviewError) as captured:
        _answer_many(
            _answer_many_args(ids="q_1,q_2", type=None, confirm_count=1),
            tmp_path,
            workspace,
        )

    assert captured.value.code == "review_answer_many_count_mismatch"
    assert load_draft(workspace, 88) == original


def test_queue_next_uses_explicit_ascending_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def pull(number: int) -> PullSnapshot:
        return PullSnapshot(
            number=number,
            issue_number=number - 1,
            title=f"投稿 {number}",
            url=f"https://github.test/pull/{number}",
            branch=f"batch/issue-{number - 1}",
            head_sha=str(number % 10) * 40,
            base_sha="b" * 40,
            draft=True,
            status_label="status:manual-review",
        )

    class Client:
        def list_open_batch_pulls(self):
            return [pull(12), pull(10), pull(11)]

    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: Client())
    args = argparse.Namespace(
        status_label=None,
        query=None,
        next=True,
        limit=None,
        fields=None,
    )

    result = _queue(args, tmp_path, tmp_path / "workspace")

    assert result["order"] == "pr-number-ascending"
    assert result["total_count"] == 3
    assert [item["pr"] for item in result["items"]] == [10]


def test_brief_combines_plan_questions_and_creation_preview(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    pull = PullSnapshot(
        number=88,
        issue_number=87,
        title="批量审核",
        url="https://github.test/pull/88",
        branch="batch/issue-87",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )
    question = _answer_many_draft(pull, "c" * 64)["questions"][0]
    planned = {
        "schema_version": 1,
        "kind": "agent_organization_review_draft",
        "repository": "example/repository",
        "pull": pull.as_dict(),
        "manifest_sha256": "c" * 64,
        "answers": {},
        "questions": [question],
        "summary": {"groups": 1, "pending_questions": 1, "complete": False},
        "organization_change_preview": [
            {
                "action": "create",
                "id": "org_preview",
                "type": "department",
                "path": "示例大学 / 计算机学院 / 待定系",
                "source": "rule",
                "source_domains": ["example.edu"],
                "official_urls": [],
                "approved_domains": [],
            }
        ],
        "path_normalizations": [
            {
                "group_id": "org_group_1",
                "submitted_path": "示例大学 / 计算机学院 / 计算机学院",
                "resolved_path": "示例大学 / 计算机学院",
                "row_count": 3,
                "source": "rule",
                "rules": ["repeated_parent_name"],
                "path_correction_scope": None,
            }
        ],
    }

    class Client:
        def fetch_main_organizations(self):
            return []

    monkeypatch.setattr(
        "mentor_data.agent_review_cli._doctor",
        lambda args: {"ready": True, "actions": []},
    )
    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: Client())
    monkeypatch.setattr(
        "mentor_data.agent_review_cli._refresh",
        lambda client, selected_workspace, pull_number: (
            pull,
            {"kind": "batch_organization_review"},
            "c" * 64,
        ),
    )
    monkeypatch.setattr("mentor_data.agent_review_cli.plan_review", lambda **kwargs: planned)
    args = argparse.Namespace(
        root=str(tmp_path),
        repository="example/repository",
        pr=88,
        reset=False,
        fields=None,
    )

    result = _brief(args, tmp_path, workspace)

    assert result["ready"] is True
    assert result["organization_change_preview"][0]["id"] == "org_preview"
    assert result["path_normalizations"][0]["row_count"] == 3
    assert result["pending_questions"][0]["id"] == "q_1"
    assert result["next"].endswith("--id q_1")
    assert load_draft(workspace, 88)["summary"]["pending_questions"] == 1


def test_check_includes_path_normalizations_in_preflight_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    pull = PullSnapshot(
        number=88,
        issue_number=87,
        title="批量审核",
        url="https://github.test/pull/88",
        branch="batch/issue-87",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )
    path_normalizations = [
        {
            "group_id": "org_group_1",
            "submitted_path": "示例大学 / 计算机学院 / 计算机学院",
            "resolved_path": "示例大学 / 计算机学院",
            "row_count": 3,
            "source": "rule",
            "rules": ["repeated_parent_name"],
            "path_correction_scope": None,
        }
    ]
    draft = {
        "schema_version": 1,
        "kind": "agent_organization_review_draft",
        "repository": "example/repository",
        "pull": pull.as_dict(),
        "manifest_sha256": "c" * 64,
        "decision": {"decisions": []},
        "path_normalizations": path_normalizations,
    }
    save_draft(workspace, draft)
    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: object())
    monkeypatch.setattr(
        "mentor_data.agent_review_cli._refresh",
        lambda client, selected_workspace, pull_number: (
            pull,
            {"kind": "batch_organization_review"},
            "c" * 64,
        ),
    )
    monkeypatch.setattr(
        "mentor_data.agent_review_cli.run_preflight",
        lambda **kwargs: {"ok": True, "organization_changes": []},
    )
    args = argparse.Namespace(
        root=str(tmp_path),
        repository="example/repository",
        pr=88,
        fields=None,
    )

    result = _check(args, tmp_path, workspace)

    assert result["ok"] is True
    assert result["path_normalizations"] == path_normalizations


def test_questions_details_returns_compact_batch_decision_packets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pull = PullSnapshot(
        number=88,
        issue_number=87,
        title="批量审核",
        url="https://github.test/pull/88",
        branch="batch/issue-87",
        head_sha="a" * 40,
        base_sha="b" * 40,
        draft=True,
        status_label="status:manual-review",
    )
    draft = _answer_many_draft(pull, "c" * 64)
    save_draft(workspace, draft)
    cache_snapshot(
        workspace,
        pull,
        {
            "kind": "batch_organization_review",
            "groups": [
                {
                    "id": f"group_{index}",
                    "rows": [{"proposal_id": f"proposal_{index}"}],
                    "source_domains": ["cs.example.edu"],
                    "source_urls": [
                        "https://cs.example.edu/faculty/list.htm",
                        f"https://cs.example.edu/faculty/{index}.htm",
                    ],
                }
                for index in (1, 2)
            ],
        },
    )
    args = argparse.Namespace(
        pr=88,
        status="pending",
        type=None,
        level=None,
        query=None,
        details=True,
        fields=None,
    )

    result = _questions(args, workspace)

    assert result["count"] == 2
    first = result["items"][0]
    assert first["row_count"] == 1
    assert first["source_domains"] == ["cs.example.edu"]
    assert len(first["source_url_samples"]) == 2
    assert first["options"][0]["requires"] == ["organization_id"]
    assert first["options"][0]["command"].endswith(
        "--choice map-existing --organization-id <organization_id>"
    )


def test_status_waits_internally_until_published(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pending = {
        "pr": 88,
        "pr_state": "open",
        "merged": False,
        "merged_at": None,
        "updated_at": "2026-08-12T10:00:00Z",
        "labels": ["status:manual-review"],
        "review_comments": 1,
        "checks": {"total": 1, "pending": 1, "failed": 0},
    }
    cleanup_pending = {
        **pending,
        "pr_state": "closed",
        "merged": True,
        "merged_at": "2026-08-12T10:01:00Z",
        "issue": 87,
        "issue_state": "open",
        "checks": {"total": 1, "pending": 0, "failed": 0},
    }
    published = {**cleanup_pending, "issue_state": "closed"}

    class Client:
        def __init__(self):
            self.values = [pending, cleanup_pending, published]

        def status(self, pull_number, *, issue_number=None):
            return self.values.pop(0)

    monotonic_values = iter([0.0, 0.0, 1.0, 2.0])
    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: Client())
    monkeypatch.setattr(
        "mentor_data.agent_review_cli.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr("mentor_data.agent_review_cli.time.sleep", lambda seconds: None)
    args = argparse.Namespace(
        pr=88,
        wait=True,
        timeout_seconds=10,
        poll_seconds=1,
        fields=None,
    )

    result = _status(args, tmp_path, tmp_path / "workspace")

    assert result["phase"] == "published"
    assert result["terminal"] is True
    assert result["outcome"] == "merged-published"
    assert result["last_transition_at"] == "2026-08-12T10:01:00Z"
    assert result["next_poll_seconds"] is None


def test_status_classifies_attention_and_workflow_failure() -> None:
    base = {
        "pr_state": "open",
        "merged": False,
        "merged_at": None,
        "updated_at": "2026-08-12T10:00:00Z",
        "labels": [],
        "review_comments": 1,
        "checks": {"total": 1, "pending": 0, "failed": 0},
    }

    attention = _classify_status(
        {**base, "labels": ["status:needs-attention"]},
        next_poll_seconds=5,
    )
    failure = _classify_status(
        {**base, "checks": {"total": 1, "pending": 0, "failed": 1}},
        next_poll_seconds=5,
    )
    action_failure = _classify_status(
        {
            **base,
            "promotion_run": {
                "status": "completed",
                "conclusion": "failure",
                "updated_at": "2026-08-12T10:02:00Z",
            },
        },
        next_poll_seconds=5,
    )

    assert attention["outcome"] == "needs-attention"
    assert failure["outcome"] == "workflow-failure"
    assert action_failure["outcome"] == "workflow-failure"
    assert attention["terminal"] is failure["terminal"] is action_failure["terminal"] is True


def test_status_wait_returns_timeout_as_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pending = {
        "pr": 88,
        "pr_state": "open",
        "merged": False,
        "merged_at": None,
        "updated_at": "2026-08-12T10:00:00Z",
        "labels": ["status:manual-review"],
        "review_comments": 1,
        "checks": {"total": 1, "pending": 1, "failed": 0},
    }

    class Client:
        def status(self, pull_number, *, issue_number=None):
            return dict(pending)

    monotonic_values = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("mentor_data.agent_review_cli._client", lambda args, root: Client())
    monkeypatch.setattr(
        "mentor_data.agent_review_cli.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr("mentor_data.agent_review_cli.time.sleep", lambda seconds: None)
    args = argparse.Namespace(
        pr=88,
        wait=True,
        timeout_seconds=1,
        poll_seconds=1,
        fields=None,
    )

    result = _status(args, tmp_path, tmp_path / "workspace")

    assert result["phase"] == "timeout"
    assert result["terminal"] is True
    assert result["outcome"] == "timeout"
    assert result["next_poll_seconds"] is None
