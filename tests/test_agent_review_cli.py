from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mentor_data.agent_review import AgentReviewError
from mentor_data.agent_review_cli import _discover_root, _doctor, _root


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
