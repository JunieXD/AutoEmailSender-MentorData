from __future__ import annotations

import json
from datetime import UTC, datetime

from mentor_data.builder import build_dataset
from mentor_data.github_events import GitHubActor, load_issue_event
from mentor_data.proposals import create_mentor_proposal, finalize_proposal
from mentor_data.repository import load_repository
from mentor_data.resolutions import apply_resolution
from mentor_data.revocation import revoke_contributor

from .helpers import build_test_repository, fixed_datetime


def _contribution_event(tmp_path):
    sections = {
        "导师姓名": "端到端导师",
        "公开工作邮箱": "trial@example.edu",
        "社区机构 ID": "org_example_cs",
        "学校正式名称": "示例大学",
        "学院或研究院正式名称": "计算机学院",
        "系所或中心": "_No response_",
        "职称": "教授",
        "研究方向": "可信系统",
        "近期或代表论文": "A Trial Paper",
        "官方个人主页": "https://cs.example.edu/faculty/trial",
        "官方证据页面": "https://cs.example.edu/faculty/trial",
        "投稿确认": "- [x] 我确认提交的是公开职业信息",
    }
    body = "\n\n".join(f"### {label}\n\n{value}" for label, value in sections.items())
    path = tmp_path / "contribution-event.json"
    path.write_text(
        json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": 40,
                    "html_url": "https://github.com/example/repository/issues/40",
                    "title": "[导师投稿] 端到端导师",
                    "body": body,
                    "created_at": "2026-08-03T00:00:00Z",
                    "user": {"id": 8040, "login": "trial-user", "type": "User"},
                    "labels": [{"name": "submission:mentor"}],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return load_issue_event(path, max_body_bytes=200_000)


def _resolution(path, *, resolution_id: str, mentor_id: str, issue_number: int, accepted):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": resolution_id,
                "mentor_id": mentor_id,
                "report_issue": {
                    "number": issue_number,
                    "url": f"https://github.com/example/repository/issues/{issue_number}",
                },
                "reporter": {"github_user_id": 9001, "github_login": "trial-reporter"},
                "decision": "accepted",
                "before": {},
                "proposed": {},
                "accepted": accepted,
                "evidence_urls": ["https://cs.example.edu/faculty/trial"],
                "moderator": {"github_user_id": 999, "github_login": "maintainer"},
                "decided_at": "2026-08-03T03:00:00Z",
                "reason": "端到端固定夹具裁决",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_full_trial_contribute_publish_correct_retire_and_revoke(tmp_path) -> None:
    root = build_test_repository(tmp_path)
    actor = GitHubActor(
        user_id=8040,
        login="trial-user",
        user_type="User",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    proposal = create_mentor_proposal(
        root,
        _contribution_event(tmp_path),
        actor,
        output_directory=tmp_path / "proposals",
    )
    finalize_proposal(root, proposal.path, moderator_github_user_id=999)
    mentor_id = load_repository(root).mentors[0]["id"]

    first_build = build_dataset(root, tmp_path / "first-dist", generated_at=fixed_datetime())
    first_catalog = json.loads(
        (tmp_path / "first-dist" / first_build["catalog_path"]).read_text(encoding="utf-8")
    )
    assert first_catalog["record_count"] == 1

    title_resolution = _resolution(
        tmp_path / "title-resolution.json",
        resolution_id="resolution_trial_title_001",
        mentor_id=mentor_id,
        issue_number=41,
        accepted={"title": "副教授"},
    )
    apply_resolution(root, title_resolution)
    assert load_repository(root).mentors[0]["title"] == "副教授"

    retirement_resolution = _resolution(
        tmp_path / "retirement-resolution.json",
        resolution_id="resolution_trial_retired_001",
        mentor_id=mentor_id,
        issue_number=42,
        accepted={
            "status": "retired",
            "status_reason": "官网确认退休",
            "status_source_url": "https://cs.example.edu/faculty/trial",
            "status_observed_at": "2026-08-03T03:00:00Z",
        },
    )
    apply_resolution(root, retirement_resolution)
    second_build = build_dataset(root, tmp_path / "second-dist", generated_at=fixed_datetime())
    second_catalog = json.loads(
        (tmp_path / "second-dist" / second_build["catalog_path"]).read_text(encoding="utf-8")
    )
    assert second_catalog["record_count"] == 0

    revoke_contributor(
        root,
        github_user_id=8040,
        reason_code="deliberate_fabrication",
        source_issue_url="https://github.com/example/repository/issues/43",
        block_scopes=["contribute", "report"],
        apply=True,
    )
    final_data = load_repository(root)
    assert final_data.mentors == []
    assert final_data.claims == []
    assert len(final_data.resolutions) == 2
