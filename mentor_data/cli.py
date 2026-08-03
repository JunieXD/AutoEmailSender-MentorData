from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .batch import create_batch_proposals, parse_batch_form
from .builder import build_dataset
from .errors import MentorDataError, RepositoryValidationError
from .github_events import GitHubActor, fetch_github_actor, load_issue_event, parse_datetime
from .io_utils import load_json, load_yaml
from .proposals import (
    check_proposal,
    check_proposal_set,
    create_mentor_proposal,
    finalize_proposal,
    finalize_proposal_set,
    proposal_paths,
)
from .reporting import (
    check_report_proposal,
    create_report_proposal,
    finalize_report_proposal,
)
from .repository import validate_repository
from .resolutions import apply_resolution
from .revocation import revoke_contributor
from .uploads import (
    download_github_attachment,
    extract_github_attachment,
    parse_community_package,
)


def _repository_root(value: str | None) -> Path:
    return Path(value or ".").resolve()


def _parse_generated_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mentor-data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="校验仓库 Schema 和语义约束")
    validate_parser.add_argument("--root")

    build_parser_command = subparsers.add_parser("build", help="构建版本化 Pages 数据集")
    build_parser_command.add_argument("--root")
    build_parser_command.add_argument("--output", required=True)
    build_parser_command.add_argument("--generated-at")

    inspect_parser = subparsers.add_parser("inspect-package", help="安全检查批量共享包")
    inspect_parser.add_argument("path")
    inspect_parser.add_argument("--root")

    prepare_parser = subparsers.add_parser(
        "prepare-issue",
        help="从 GitHub Issue webhook 生成安全审核提案",
    )
    prepare_parser.add_argument("--event", required=True)
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--root")
    prepare_parser.add_argument(
        "--actor-json",
        help="仅用于测试的 GitHub 用户 API JSON；生产环境不传时查询官方 API",
    )

    prepare_batch_parser = subparsers.add_parser(
        "prepare-batch",
        help="从 GitHub 批量投稿 Issue 下载安全共享包并生成逐行审核提案",
    )
    prepare_batch_parser.add_argument("--event", required=True)
    prepare_batch_parser.add_argument("--output", required=True)
    prepare_batch_parser.add_argument("--root")
    prepare_batch_parser.add_argument("--actor-json")
    prepare_batch_parser.add_argument(
        "--package",
        help="仅用于本地测试；生产环境省略并从经过校验的 GitHub 附件下载",
    )

    finalize_parser = subparsers.add_parser(
        "finalize-proposal",
        help="把已经审核的提案写入 Claim 和规范导师实体",
    )
    finalize_parser.add_argument("proposal")
    finalize_parser.add_argument("--moderator-id", type=int)
    finalize_parser.add_argument("--root")

    check_proposal_parser = subparsers.add_parser(
        "check-proposal",
        help="在临时仓库中预演审核提案是否可以安全落库",
    )
    check_proposal_parser.add_argument("proposal")
    check_proposal_parser.add_argument("--root")

    check_proposal_set_parser = subparsers.add_parser(
        "check-proposal-set",
        help="按顺序预演仓库中的全部待审核导师提案",
    )
    check_proposal_set_parser.add_argument("--root")

    finalize_proposal_set_parser = subparsers.add_parser(
        "finalize-proposal-set",
        help="按顺序应用指定目录中的全部导师提案",
    )
    finalize_proposal_set_parser.add_argument("directory")
    finalize_proposal_set_parser.add_argument("--moderator-id", type=int)
    finalize_proposal_set_parser.add_argument("--root")

    prepare_report_parser = subparsers.add_parser(
        "prepare-report",
        help="从 GitHub 错误反馈 Issue 生成待审核提案",
    )
    prepare_report_parser.add_argument("--event", required=True)
    prepare_report_parser.add_argument("--output", required=True)
    prepare_report_parser.add_argument("--root")
    prepare_report_parser.add_argument("--actor-json")

    check_report_parser = subparsers.add_parser(
        "check-report-proposal",
        help="预演反馈裁决能否安全应用",
    )
    check_report_parser.add_argument("proposal")
    check_report_parser.add_argument("--root")

    finalize_report_parser = subparsers.add_parser(
        "finalize-report-proposal",
        help="把已审核反馈提案转换为 Resolution 并应用",
    )
    finalize_report_parser.add_argument("proposal")
    finalize_report_parser.add_argument("--moderator-id", type=int, required=True)
    finalize_report_parser.add_argument("--moderator-login", required=True)
    finalize_report_parser.add_argument("--root")

    resolution_parser = subparsers.add_parser(
        "apply-resolution",
        help="应用已审核的纠错或生命周期裁决",
    )
    resolution_parser.add_argument("resolution")
    resolution_parser.add_argument("--root")

    revoke_parser = subparsers.add_parser(
        "revoke-contributor",
        help="预演或执行按 GitHub 数字 ID 撤销全部贡献",
    )
    revoke_parser.add_argument("--github-user-id", type=int, required=True)
    revoke_parser.add_argument("--reason-code", required=True)
    revoke_parser.add_argument("--source-issue-url")
    revoke_parser.add_argument(
        "--block-scope",
        action="append",
        choices=["contribute", "report"],
        default=[],
    )
    revoke_parser.add_argument("--apply", action="store_true")
    revoke_parser.add_argument("--root")

    return parser


def _actor_from_json(path: Path) -> GitHubActor:
    value = load_json(path)
    user_id = value.get("id")
    login = value.get("login")
    user_type = value.get("type")
    if (
        not isinstance(user_id, int)
        or not isinstance(login, str)
        or user_type not in {"User", "Bot"}
    ):
        raise ValueError("actor JSON 缺少有效 id、login 或 type")
    return GitHubActor(
        user_id=user_id,
        login=login,
        user_type=user_type,
        created_at=parse_datetime(str(value.get("created_at", ""))),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = _repository_root(getattr(args, "root", None))
        if args.command == "validate":
            data = validate_repository(root)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "organizations": len(data.registry.organizations),
                        "mentors": len(data.mentors),
                        "claims": len(data.claims),
                        "resolutions": len(data.resolutions),
                        "proposals": len(data.proposals),
                        "report_proposals": len(data.report_proposals),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "build":
            latest = build_dataset(
                root,
                Path(args.output),
                generated_at=_parse_generated_at(args.generated_at),
            )
            print(json.dumps(latest, ensure_ascii=False))
            return 0
        if args.command == "inspect-package":
            policy = load_yaml(root / "registry" / "policy.yml")
            records = parse_community_package(Path(args.path), policy)
            print(json.dumps({"ok": True, "records": records}, ensure_ascii=False))
            return 0
        if args.command == "prepare-issue":
            policy = load_yaml(root / "registry" / "policy.yml")
            event = load_issue_event(
                Path(args.event),
                max_body_bytes=policy["limits"]["max_issue_body_bytes"],
            )
            actor = (
                _actor_from_json(Path(args.actor_json))
                if args.actor_json
                else fetch_github_actor(event.author_login)
            )
            result = create_mentor_proposal(
                root,
                event,
                actor,
                output_directory=Path(args.output),
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "proposal": str(result.path),
                        "auto_eligible": result.proposal["auto_eligible"],
                        "review_reasons": result.proposal["review_reasons"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "prepare-batch":
            policy = load_yaml(root / "registry" / "policy.yml")
            event = load_issue_event(
                Path(args.event),
                max_body_bytes=policy["limits"]["max_issue_body_bytes"],
            )
            actor = (
                _actor_from_json(Path(args.actor_json))
                if args.actor_json
                else fetch_github_actor(event.author_login)
            )
            sections = parse_batch_form(event)
            attachment = extract_github_attachment(sections["社区共享包"])
            with tempfile.TemporaryDirectory(prefix="mentor-data-upload-") as temporary:
                package_path = (
                    Path(args.package)
                    if args.package
                    else download_github_attachment(
                        attachment,
                        Path(temporary),
                        max_bytes=policy["limits"]["max_upload_bytes"],
                    )
                )
                result = create_batch_proposals(
                    root,
                    event,
                    actor,
                    package_path=package_path,
                    output_directory=Path(args.output),
                )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "proposal_count": len(result.paths),
                        "all_auto_eligible": result.all_auto_eligible,
                        "proposals": [str(path) for path in result.paths],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "finalize-proposal":
            claim_path, mentor_path = finalize_proposal(
                root,
                Path(args.proposal),
                moderator_github_user_id=args.moderator_id,
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "claim": str(claim_path),
                        "mentor": str(mentor_path),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "check-proposal":
            check_proposal(root, Path(args.proposal))
            print(json.dumps({"ok": True, "proposal": args.proposal}, ensure_ascii=False))
            return 0
        if args.command == "check-proposal-set":
            paths = proposal_paths(root)
            check_proposal_set(root, paths)
            print(json.dumps({"ok": True, "proposal_count": len(paths)}, ensure_ascii=False))
            return 0
        if args.command == "finalize-proposal-set":
            directory = Path(args.directory)
            paths = sorted(directory.rglob("*.json"))
            results = finalize_proposal_set(
                root,
                paths,
                moderator_github_user_id=args.moderator_id,
            )
            print(json.dumps({"ok": True, "proposal_count": len(results)}, ensure_ascii=False))
            return 0
        if args.command == "prepare-report":
            policy = load_yaml(root / "registry" / "policy.yml")
            event = load_issue_event(
                Path(args.event),
                max_body_bytes=policy["limits"]["max_issue_body_bytes"],
            )
            actor = (
                _actor_from_json(Path(args.actor_json))
                if args.actor_json
                else fetch_github_actor(event.author_login)
            )
            report_path = create_report_proposal(
                root,
                event,
                actor,
                output_directory=Path(args.output),
            )
            print(json.dumps({"ok": True, "proposal": str(report_path)}, ensure_ascii=False))
            return 0
        if args.command == "check-report-proposal":
            check_report_proposal(root, Path(args.proposal))
            print(json.dumps({"ok": True, "proposal": args.proposal}, ensure_ascii=False))
            return 0
        if args.command == "finalize-report-proposal":
            resolution_path, mentor_path = finalize_report_proposal(
                root,
                Path(args.proposal),
                moderator_github_user_id=args.moderator_id,
                moderator_github_login=args.moderator_login,
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "resolution": str(resolution_path),
                        "mentor": str(mentor_path) if mentor_path else None,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "apply-resolution":
            resolution_path, mentor_path = apply_resolution(root, Path(args.resolution))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "resolution": str(resolution_path),
                        "mentor": str(mentor_path) if mentor_path else None,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "revoke-contributor":
            result = revoke_contributor(
                root,
                github_user_id=args.github_user_id,
                reason_code=args.reason_code,
                source_issue_url=args.source_issue_url,
                block_scopes=args.block_scope,
                apply=args.apply,
            )
            print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            return 0
    except RepositoryValidationError as error:
        for issue in error.issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 2
    except (MentorDataError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
