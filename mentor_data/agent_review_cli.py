from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from .agent_review import (
    AgentReviewError,
    assert_draft_current,
    cache_snapshot,
    canonical_json_sha256,
    decision_comment_body,
    draft_path,
    load_cached_manifest,
    load_draft,
    manifest_summary,
    plan_review,
    project_fields,
    save_draft,
    sha256_bytes,
    validate_answer,
)
from .agent_review_github import GitHubReviewClient
from .agent_review_preflight import run_preflight

DEFAULT_REPOSITORY = "JunieXD/AutoEmailSender-MentorData"


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub 仓库（默认：{DEFAULT_REPOSITORY}）",
    )
    parser.add_argument("--root", help="本地可信 MentorData 仓库根目录")
    parser.add_argument(
        "--workspace",
        help="本地审核底稿目录（默认：<root>/.work/agent-reviews）",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="输出格式；json 为紧凑 Agent 输出（默认：json）",
    )
    return parser


def _add_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fields",
        help="逗号分隔的精确输出字段；未知字段会列出全部可选字段",
    )


def register_review_parser(subparsers: argparse._SubParsersAction) -> None:
    common = _common_parser()
    review = subparsers.add_parser(
        "review",
        help="面向 Agent 的批量投稿机构规范化审核",
        description=(
            "在不使用浏览器、不默认核验导师个人资料内容的前提下，按机构路径规划批量投稿审核。"
            "确定项由保守规则完成，歧义项生成稳定问题；正式评论只能通过 submit 发布。"
        ),
        epilog=(
            "推荐流程：\n"
            "  mentor-data review queue\n"
            "  mentor-data review inspect --pr 81\n"
            "  mentor-data review plan --pr 81\n"
            "  mentor-data review organizations --pr 81 --query '西电 计算机'\n"
            "  mentor-data review questions --pr 81 --status pending\n"
            "  mentor-data review answer --pr 81 --id q_x --choice use-parent\n"
            "  mentor-data review check --pr 81\n"
            "  mentor-data review submit --pr 81 --confirm-pr 81\n\n"
            "安全边界：inspect/plan/groups/questions/answer/check 均不会写入 GitHub。"
            "submit 会发布最终审核评论并触发现有可信落库队列。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = review.add_subparsers(dest="review_command", required=True)

    queue = commands.add_parser(
        "queue",
        parents=[common],
        help="列出开放的内部批量投稿 PR",
        description="只返回 PR 标识、标题、审核规模和本地底稿状态，不读取导师行详情。",
    )
    queue.add_argument(
        "--status-label",
        choices=["status:manual-review", "status:auto-eligible"],
        help="仅返回指定处理标签",
    )
    queue.add_argument("--query", help="按 PR 编号或标题包含文本筛选")
    _add_fields(queue)

    inspect = commands.add_parser(
        "inspect",
        parents=[common],
        help="读取一个 PR 的低 Token 审核摘要",
        description="校验开放内部批量 PR 和审核清单，并刷新本地只读缓存。",
    )
    inspect.add_argument("--pr", type=int, required=True, help="批量投稿 PR 编号")
    _add_fields(inspect)

    plan = commands.add_parser(
        "plan",
        parents=[common],
        help="运行确定性规则并创建人机协作底稿",
        description=(
            "自动处理唯一精确匹配、明确新机构、空层级和重复祖先等确定项；"
            "学院层级歧义、来源域名变更和导师冲突会生成人工问题。"
        ),
    )
    plan.add_argument("--pr", type=int, required=True)
    plan.add_argument(
        "--reset",
        action="store_true",
        help="清除旧答案并基于当前 PR 重新规划；PR 变化后必须显式使用",
    )
    _add_fields(plan)

    groups = commands.add_parser(
        "groups",
        parents=[common],
        help="筛选机构路径分组",
        description="默认不输出导师行；用 group --id 查看单个分组的必要详情。",
    )
    groups.add_argument("--pr", type=int, required=True)
    groups.add_argument("--state", choices=["auto", "pending", "answered", "all"], default="all")
    groups.add_argument("--rule", help="只返回包含指定自动规则代码的分组")
    groups.add_argument("--query", help="按机构路径包含文本筛选")
    _add_fields(groups)

    group = commands.add_parser(
        "group",
        parents=[common],
        help="读取一个机构路径分组的必要上下文",
    )
    group.add_argument("--pr", type=int, required=True)
    group.add_argument("--id", required=True, help="org_group_... 分组 ID")
    _add_fields(group)

    organizations = commands.add_parser(
        "organizations",
        parents=[common],
        help="精确搜索审核清单中的现有机构",
        description=(
            "用于回答 map-existing 等问题。默认只返回 ID、类型、完整路径、父级和批准域名；"
            "查询中的每个空格分词都必须在名称、别名、路径、ID 或域名中出现。"
        ),
    )
    organizations.add_argument("--pr", type=int, required=True)
    organizations.add_argument("--query", help="空格分隔的机构路径、名称、ID 或域名关键词")
    organizations.add_argument(
        "--level",
        choices=["university", "school", "department"],
        help="按机构树层级筛选",
    )
    organizations.add_argument("--parent-id", help="只返回指定直接父级下的机构")
    organizations.add_argument("--domain", help="只返回包含指定批准域名的机构")
    _add_fields(organizations)

    invalid_rows = commands.add_parser(
        "invalid-rows",
        parents=[common],
        help="列出解析阶段退出提案的普通无效行",
        description="默认只返回批次行号、原因代码和短消息，不输出整行原始内容。",
    )
    invalid_rows.add_argument("--pr", type=int, required=True)
    invalid_rows.add_argument("--reason-code")
    _add_fields(invalid_rows)

    questions = commands.add_parser(
        "questions",
        parents=[common],
        help="筛选需要用户裁决的问题",
        description="默认只输出待回答问题的短摘要、建议选择和可用选择值。",
    )
    questions.add_argument("--pr", type=int, required=True)
    questions.add_argument(
        "--status",
        choices=["pending", "answered", "all"],
        default="pending",
    )
    questions.add_argument("--type", help="问题类型，例如 school_level_in_department")
    questions.add_argument(
        "--level",
        choices=["university", "school", "department", "none"],
    )
    questions.add_argument("--query", help="按路径、提示或问题 ID 包含文本筛选")
    _add_fields(questions)

    question = commands.add_parser(
        "question",
        parents=[common],
        help="读取单个问题的完整选项和最小必要上下文",
    )
    question.add_argument("--pr", type=int, required=True)
    question.add_argument("--id", required=True, help="q_... 问题 ID")
    _add_fields(question)

    answer = commands.add_parser(
        "answer",
        parents=[common],
        help="记录用户裁决并重新计算剩余问题",
        description=(
            "只修改 .work 中与当前 PR SHA 绑定的底稿，不发表评论。"
            "先运行 question 查看该选择所需参数。"
        ),
    )
    answer.add_argument("--pr", type=int, required=True)
    answer.add_argument("--id", required=True)
    answer_action = answer.add_mutually_exclusive_group(required=True)
    answer_action.add_argument("--choice", help="问题选项 value")
    answer_action.add_argument("--clear", action="store_true", help="清除该问题的已有回答")
    answer.add_argument("--organization-id")
    answer.add_argument("--organization-type")
    answer.add_argument("--canonical-name")
    answer.add_argument("--official-url")
    answer.add_argument("--approved-domain", action="append", default=[])
    answer.add_argument("--reason")
    answer.add_argument("--former-affiliation-id")
    answer.add_argument("--make-primary", action="store_true")
    answer.add_argument("--save-path-correction", action="store_true")

    check = commands.add_parser(
        "check",
        parents=[common],
        help="在最新 main 上完整预演审核决定",
        description=(
            "物化受限 PR 提案到临时 worktree，复用可信后端应用机构审核并预演全部提案。"
            "不会修改当前工作树或 GitHub。"
        ),
    )
    check.add_argument("--pr", type=int, required=True)
    _add_fields(check)

    decision = commands.add_parser(
        "decision",
        parents=[common],
        help="查看完整决定、评论正文或低 Token 摘要",
    )
    decision.add_argument("--pr", type=int, required=True)
    decision.add_argument(
        "--view",
        choices=["summary", "decision", "comment"],
        default="summary",
    )

    submit = commands.add_parser(
        "submit",
        parents=[common],
        help="再次预演后发布唯一正式审核评论",
        description=(
            "这是唯一远程写操作。要求无待回答问题、PR/清单未变化、完整预演通过，"
            "并提供与 --pr 相同的 --confirm-pr。评论会立即触发现有可信落库队列。"
        ),
    )
    submit.add_argument("--pr", type=int, required=True)
    submit.add_argument("--confirm-pr", type=int, required=True)

    status = commands.add_parser(
        "status",
        parents=[common],
        help="查看审核评论、Actions、PR 和来源 Issue 状态",
    )
    status.add_argument("--pr", type=int, required=True)
    _add_fields(status)


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root or ".").resolve()


def _workspace(args: argparse.Namespace, root: Path) -> Path:
    return Path(args.workspace).resolve() if args.workspace else root / ".work" / "agent-reviews"


def _fields(args: argparse.Namespace) -> list[str] | None:
    value = getattr(args, "fields", None)
    if not value:
        return None
    fields = [item.strip() for item in value.split(",") if item.strip()]
    if not fields:
        raise AgentReviewError("review_fields_invalid", "--fields 没有包含有效字段")
    return list(dict.fromkeys(fields))


def _emit(command: str, data: Any, *, output_format: str) -> None:
    envelope = {"ok": True, "command": f"review.{command}", "data": data}
    if output_format == "text":
        print(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))


def _emit_error(error: AgentReviewError, *, output_format: str) -> None:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": error.code, "message": error.message},
    }
    if error.next_command:
        payload["error"]["next"] = error.next_command
    if output_format == "text":
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)


def _client(args: argparse.Namespace, root: Path) -> GitHubReviewClient:
    return GitHubReviewClient(repository=args.repository, root=root)


def _refresh(
    client: GitHubReviewClient,
    workspace: Path,
    pull_number: int,
) -> tuple[Any, dict[str, Any], str]:
    pull, manifest, payload = client.fetch_review_bundle(pull_number)
    manifest_sha256 = sha256_bytes(payload)
    cache_snapshot(workspace, pull, manifest)
    return pull, manifest, manifest_sha256


def _require_decision(draft: dict[str, Any], pull_number: int) -> dict[str, Any]:
    decision = draft.get("decision")
    if not isinstance(decision, dict):
        pending = draft.get("summary", {}).get("pending_questions", 0)
        raise AgentReviewError(
            "review_questions_pending",
            f"还有 {pending} 个问题未解决",
            next_command=f"mentor-data review questions --pr {pull_number} --status pending",
        )
    return decision


def _find_question(draft: dict[str, Any], question_id: str) -> dict[str, Any]:
    question = next(
        (item for item in draft.get("questions", []) if item.get("id") == question_id),
        None,
    )
    if question is None:
        raise AgentReviewError(
            "review_question_not_found",
            f"审核底稿中没有问题 {question_id}",
        )
    return question


def _queue(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    pulls = _client(args, root).list_open_batch_pulls()
    query = (args.query or "").casefold()
    items: list[dict[str, Any]] = []
    for pull in pulls:
        if args.status_label and pull.status_label != args.status_label:
            continue
        if query and query not in f"{pull.number} {pull.title}".casefold():
            continue
        item = {
            "pr": pull.number,
            "issue": pull.issue_number,
            "title": pull.title,
            "draft": pull.draft,
            "status_label": pull.status_label,
            "version": pull.head_sha[:12],
            "local_state": "unplanned",
            "pending_questions": None,
            "groups": None,
            "rows": None,
        }
        path = draft_path(workspace, pull.number)
        if path.is_file():
            try:
                draft = load_draft(workspace, pull.number)
                if draft["pull"].get("head_sha") != pull.head_sha:
                    item["local_state"] = "stale"
                else:
                    pending = draft.get("summary", {}).get("pending_questions", 0)
                    item["local_state"] = "ready" if pending == 0 else "questions"
                    item["pending_questions"] = pending
                    item["groups"] = draft.get("summary", {}).get("groups")
                    item["rows"] = draft.get("summary", {}).get("rows")
            except AgentReviewError:
                item["local_state"] = "invalid"
        items.append(project_fields(item, _fields(args)))
    return {"count": len(items), "items": items}


def _inspect(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    client = _client(args, root)
    pull, manifest, digest = _refresh(client, workspace, args.pr)
    summary = manifest_summary(pull, manifest, digest)
    path = draft_path(workspace, args.pr)
    summary["local_state"] = "unplanned"
    if path.is_file():
        try:
            draft = load_draft(workspace, args.pr)
            summary["local_state"] = (
                "current"
                if draft["pull"].get("head_sha") == pull.head_sha
                and draft.get("manifest_sha256") == digest
                else "stale"
            )
        except AgentReviewError:
            summary["local_state"] = "invalid"
    return project_fields(summary, _fields(args))


def _plan(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    client = _client(args, root)
    pull, manifest, digest = _refresh(client, workspace, args.pr)
    previous_answers: dict[str, Any] = {}
    path = draft_path(workspace, args.pr)
    if path.is_file() and not args.reset:
        previous = load_draft(workspace, args.pr)
        assert_draft_current(previous, pull, digest)
        previous_answers = previous.get("answers", {})
    draft = plan_review(
        repository=args.repository,
        pull=pull,
        manifest=manifest,
        manifest_sha256=digest,
        previous_answers=previous_answers,
    )
    save_draft(workspace, draft)
    result = {
        "pr": args.pr,
        **draft["summary"],
        "next": (
            f"mentor-data review questions --pr {args.pr} --status pending"
            if draft["summary"]["pending_questions"]
            else f"mentor-data review check --pr {args.pr}"
        ),
    }
    return project_fields(result, _fields(args))


def _groups(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    draft = load_draft(workspace, args.pr)
    query = (args.query or "").casefold()
    items: list[dict[str, Any]] = []
    for item in draft.get("groups", []):
        if args.state != "all" and item.get("state") != args.state:
            continue
        if args.rule and args.rule not in item.get("auto_rules", []):
            continue
        if query and query not in f"{item['id']} {item['path']}".casefold():
            continue
        summary = {
            "id": item["id"],
            "path": item["path"],
            "row_count": item["row_count"],
            "state": item["state"],
            "auto_rules": item["auto_rules"],
            "question_count": len(item["question_ids"]),
        }
        items.append(project_fields(summary, _fields(args)))
    return {"count": len(items), "items": items}


def _group(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    draft = load_draft(workspace, args.pr)
    manifest = load_cached_manifest(workspace, args.pr)
    group = next((item for item in manifest["groups"] if item["id"] == args.id), None)
    plan = next((item for item in draft["groups"] if item["id"] == args.id), None)
    if group is None or plan is None:
        raise AgentReviewError("review_group_not_found", f"没有机构分组 {args.id}")
    rows = group["rows"]
    value = {
        "id": group["id"],
        "path": plan["path"],
        "state": plan["state"],
        "submitted": group["submitted"],
        "row_count": len(rows),
        "source_domains": group["source_domains"],
        "source_url_count": len(group["source_urls"]),
        "source_url_samples": group["source_urls"][:3],
        "review_reasons": group["review_reasons"],
        "suggested_organization_id": group.get("suggested_organization_id"),
        "suggested_path_correction": group.get("suggested_path_correction"),
        "identity_conflicts": sum(isinstance(row.get("identity"), dict) for row in rows),
        "record_conflicts": sum(isinstance(row.get("record_conflict"), dict) for row in rows),
        "auto_rules": plan["auto_rules"],
        "question_ids": plan["question_ids"],
    }
    return project_fields(value, _fields(args))


def _organization_level(organization_type: str) -> str:
    if organization_type == "university":
        return "university"
    if organization_type in {"school", "institute"}:
        return "school"
    return "department"


def _organizations(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    manifest = load_cached_manifest(workspace, args.pr)
    if not any((args.query, args.level, args.parent_id, args.domain)):
        raise AgentReviewError(
            "review_filter_required",
            "机构搜索至少需要 --query、--level、--parent-id 或 --domain 之一",
            next_command=f"mentor-data review organizations --pr {args.pr} --query '<学校 学院>'",
        )
    tokens = [item.casefold() for item in (args.query or "").split() if item]
    domain = (args.domain or "").casefold().rstrip(".")
    items: list[dict[str, Any]] = []
    for organization in manifest["organizations"]:
        if args.level and _organization_level(organization["type"]) != args.level:
            continue
        if args.parent_id and organization.get("parent_id") != args.parent_id:
            continue
        domains = organization.get("approved_domains", [])
        if domain and domain not in {item.casefold().rstrip(".") for item in domains}:
            continue
        searchable = " ".join(
            [
                organization["id"],
                organization["canonical_name"],
                *organization.get("aliases", []),
                *organization.get("lineage_names", []),
                *domains,
            ]
        ).casefold()
        if tokens and not all(token in searchable for token in tokens):
            continue
        value = {
            "id": organization["id"],
            "type": organization["type"],
            "name": organization["canonical_name"],
            "path": " / ".join(organization.get("lineage_names", [])),
            "parent_id": organization.get("parent_id"),
            "aliases": organization.get("aliases", []),
            "approved_domains": domains,
        }
        items.append(project_fields(value, _fields(args)))
    return {"count": len(items), "items": items}


def _invalid_rows(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    manifest = load_cached_manifest(workspace, args.pr)
    items: list[dict[str, Any]] = []
    for row in manifest.get("invalid_rows", []):
        if args.reason_code and row.get("reason_code") != args.reason_code:
            continue
        value = {
            "batch_row": row["batch_row"],
            "sheet_row": row["sheet_row"],
            "reason_code": row["reason_code"],
            "message": row["message"],
        }
        items.append(project_fields(value, _fields(args)))
    return {"count": len(items), "items": items}


def _question_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "group_id": item["group_id"],
        "type": item["type"],
        "level": item["level"],
        "path": item["path"],
        "status": item["status"],
        "prompt": item["prompt"],
        "reason": item["reason"],
        "recommendation": item["recommendation"],
        "choices": [option["value"] for option in item["options"]],
    }


def _questions(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    draft = load_draft(workspace, args.pr)
    query = (args.query or "").casefold()
    items: list[dict[str, Any]] = []
    for item in draft.get("questions", []):
        if args.status != "all" and item.get("status") != args.status:
            continue
        if args.type and item.get("type") != args.type:
            continue
        expected_level = None if args.level == "none" else args.level
        if args.level and item.get("level") != expected_level:
            continue
        searchable = f"{item['id']} {item['path']} {item['prompt']}".casefold()
        if query and query not in searchable:
            continue
        items.append(project_fields(_question_summary(item), _fields(args)))
    return {"count": len(items), "items": items}


def _question(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    value = _find_question(load_draft(workspace, args.pr), args.id)
    return project_fields(value, _fields(args))


def _answer_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "choice": args.choice,
            "organization_id": args.organization_id,
            "organization_type": args.organization_type,
            "canonical_name": args.canonical_name,
            "official_url": args.official_url,
            "approved_domains": args.approved_domain or None,
            "reason": args.reason,
            "former_affiliation_id": args.former_affiliation_id,
            "make_primary": args.make_primary or None,
            "save_path_correction": args.save_path_correction or None,
        }.items()
        if value is not None
    }


def _answer(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    client = _client(args, root)
    pull, manifest, digest = _refresh(client, workspace, args.pr)
    previous = load_draft(workspace, args.pr)
    assert_draft_current(previous, pull, digest)
    question = _find_question(previous, args.id)
    answers = dict(previous.get("answers", {}))
    if args.clear:
        answers.pop(args.id, None)
        choice = None
    else:
        payload = _answer_payload(args)
        validate_answer(question, payload)
        answers[args.id] = payload
        choice = payload["choice"]
    draft = plan_review(
        repository=args.repository,
        pull=pull,
        manifest=manifest,
        manifest_sha256=digest,
        previous_answers=answers,
    )
    save_draft(workspace, draft)
    pending = [item for item in draft["questions"] if item["status"] == "pending"]
    return {
        "pr": args.pr,
        "question": args.id,
        "choice": choice,
        "cleared": args.clear,
        "pending_questions": len(pending),
        "next_question": pending[0]["id"] if pending else None,
        "next": (
            f"mentor-data review question --pr {args.pr} --id {pending[0]['id']}"
            if pending
            else f"mentor-data review check --pr {args.pr}"
        ),
    }


def _check(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    client = _client(args, root)
    pull, manifest, digest = _refresh(client, workspace, args.pr)
    draft = load_draft(workspace, args.pr)
    assert_draft_current(draft, pull, digest)
    decision = _require_decision(draft, args.pr)
    preflight = run_preflight(root=root, client=client, pull=pull, decision=decision)
    draft["preflight"] = preflight
    draft["submission"] = None
    save_draft(workspace, draft)
    return project_fields(preflight, _fields(args))


def _decision(args: argparse.Namespace, workspace: Path) -> Any:
    draft = load_draft(workspace, args.pr)
    decision = _require_decision(draft, args.pr)
    body = decision_comment_body(decision)
    if args.view == "decision":
        return decision
    if args.view == "comment":
        return {"body": body, "characters": len(body)}
    return {
        "pr": args.pr,
        "groups": len(decision["decisions"]),
        "organization_creations": len(decision.get("organization_creations", [])),
        "decision_sha256": canonical_json_sha256(decision),
        "comment_characters": len(body),
        "preflight_ok": draft.get("preflight", {}).get("ok") is True,
        "submitted": draft.get("submission") is not None,
    }


def _submit(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    if args.confirm_pr != args.pr:
        raise AgentReviewError(
            "review_confirmation_mismatch",
            "--confirm-pr 必须与 --pr 完全一致",
        )
    client = _client(args, root)
    pull, manifest, digest = _refresh(client, workspace, args.pr)
    draft = load_draft(workspace, args.pr)
    assert_draft_current(draft, pull, digest)
    decision = _require_decision(draft, args.pr)
    existing = client.official_review_comments(args.pr)
    if existing:
        raise AgentReviewError(
            "review_already_submitted",
            f"PR #{args.pr} 已有正式审核评论 #{existing[-1].get('id')}",
            next_command=f"mentor-data review status --pr {args.pr}",
        )
    preflight = run_preflight(root=root, client=client, pull=pull, decision=decision)
    final_pull, _, final_payload = client.fetch_review_bundle(args.pr)
    assert_draft_current(draft, final_pull, sha256_bytes(final_payload))
    if client.official_review_comments(args.pr):
        raise AgentReviewError(
            "review_already_submitted",
            f"PR #{args.pr} 在预演期间出现了正式审核评论",
            next_command=f"mentor-data review status --pr {args.pr}",
        )
    body = decision_comment_body(decision)
    comment = client.submit_review_comment(args.pr, body)
    draft["preflight"] = preflight
    draft["submission"] = {
        "comment_id": comment["id"],
        "url": comment.get("html_url"),
        "head_sha": pull.head_sha,
        "decision_sha256": canonical_json_sha256(decision),
    }
    save_draft(workspace, draft)
    return {
        "pr": args.pr,
        "comment_id": comment["id"],
        "url": comment.get("html_url"),
        "preflight": preflight,
        "next": f"mentor-data review status --pr {args.pr}",
    }


def _status(args: argparse.Namespace, root: Path, workspace: Path) -> dict[str, Any]:
    issue_number = None
    path = draft_path(workspace, args.pr)
    if path.is_file():
        with contextlib.suppress(AgentReviewError):
            issue_number = load_draft(workspace, args.pr)["pull"].get("issue_number")
    value = _client(args, root).status(args.pr, issue_number=issue_number)
    return project_fields(value, _fields(args))


def execute_review(args: argparse.Namespace) -> int:
    output_format = getattr(args, "format", "json")
    try:
        root = _root(args)
        workspace = _workspace(args, root)
        command = args.review_command
        if command == "queue":
            data = _queue(args, root, workspace)
        elif command == "inspect":
            data = _inspect(args, root, workspace)
        elif command == "plan":
            data = _plan(args, root, workspace)
        elif command == "groups":
            data = _groups(args, workspace)
        elif command == "group":
            data = _group(args, workspace)
        elif command == "organizations":
            data = _organizations(args, workspace)
        elif command == "invalid-rows":
            data = _invalid_rows(args, workspace)
        elif command == "questions":
            data = _questions(args, workspace)
        elif command == "question":
            data = _question(args, workspace)
        elif command == "answer":
            data = _answer(args, root, workspace)
        elif command == "check":
            data = _check(args, root, workspace)
        elif command == "decision":
            data = _decision(args, workspace)
        elif command == "submit":
            data = _submit(args, root, workspace)
        elif command == "status":
            data = _status(args, root, workspace)
        else:
            raise AgentReviewError("review_command_invalid", "未知审核命令")
        _emit(command, data, output_format=output_format)
        return 0
    except AgentReviewError as error:
        _emit_error(error, output_format=output_format)
        return 2
