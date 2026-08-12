from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent_review import AgentReviewError, PullSnapshot, canonical_json_sha256, utc_now
from .agent_review_github import GitHubReviewClient
from .errors import MentorDataError, RepositoryValidationError
from .internal_pulls import InternalPull, materialize_proposal_paths, proposal_paths_from_diff
from .organization_review import (
    ReviewComment,
    ReviewPull,
    apply_organization_review,
)
from .proposals import finalize_proposal_set
from .repository import load_repository


def _organization_changes(before: Any, after: Any) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    before_ids = set(before.by_id)
    compared_fields = (
        "type",
        "canonical_name",
        "parent_id",
        "aliases",
        "official_urls",
        "approved_domains",
        "status",
        "successor_id",
    )
    for organization in after.organizations:
        organization_id = organization["id"]
        previous = before.by_id.get(organization_id)
        changed_fields = {
            field: {
                "before": previous.get(field) if previous is not None else None,
                "after": organization.get(field),
            }
            for field in compared_fields
            if previous is not None and previous.get(field) != organization.get(field)
        }
        if previous is not None and not changed_fields:
            continue
        if organization_id not in before_ids:
            action = "create"
        elif (
            organization.get("status") == "merged"
            and (
                previous.get("status") != "merged"
                or previous.get("successor_id") != organization.get("successor_id")
            )
        ):
            action = "merge"
        elif previous.get("canonical_name") != organization.get("canonical_name"):
            action = "rename"
        else:
            action = "update"
        lineage = after.lineage(organization_id)
        item = {
            "action": action,
            "id": organization_id,
            "type": organization["type"],
            "path": " / ".join(item["canonical_name"] for item in lineage),
            "aliases": organization.get("aliases", []),
            "official_urls": organization.get("official_urls", []),
            "approved_domains": organization.get("approved_domains", []),
            "status": organization.get("status"),
            "successor_id": organization.get("successor_id"),
        }
        if changed_fields:
            item["changed_fields"] = changed_fields
        changes.append(item)
    return sorted(changes, key=lambda item: (item["action"], item["path"], item["id"]))


def _internal_pull(pull: PullSnapshot) -> InternalPull:
    return InternalPull(
        number=pull.number,
        url=pull.url,
        title=pull.title,
        kind="batch",
        issue_number=pull.issue_number,
        branch=pull.branch,
        head_sha=pull.head_sha,
        base_sha=pull.base_sha,
        draft=pull.draft,
        status_label=pull.status_label,
    )


def run_preflight(
    *,
    root: Path,
    client: GitHubReviewClient,
    pull: PullSnapshot,
    decision: dict[str, Any],
) -> dict[str, Any]:
    client.fetch_for_preflight(pull)
    main_sha = client._run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=root,
    ).stdout.strip()
    internal = _internal_pull(pull)
    try:
        paths = proposal_paths_from_diff(
            root,
            internal,
            runner=client.runner,
        )
    except (OSError, subprocess.CalledProcessError, MentorDataError, ValueError) as error:
        raise AgentReviewError(
            "review_preflight_failed",
            f"无法读取 PR 提案文件：{str(error).splitlines()[0]}",
        ) from error

    prefix = f"mentor-data-agent-review-{pull.number}-"
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        candidate = Path(temporary) / "candidate"
        client._run(
            ["git", "worktree", "add", "--detach", str(candidate), main_sha],
            cwd=root,
        )
        try:
            before_registry = load_repository(candidate, schema_root=root).registry
            materialize_proposal_paths(
                root,
                candidate,
                source_sha=pull.head_sha,
                paths=paths,
                runner=client.runner,
            )
            review_comment = ReviewComment(
                pull_request_number=pull.number,
                comment_id=1,
                reviewer_id=1,
                reviewer_login="agent-review-preflight",
                author_association="OWNER",
                created_at=datetime.now(UTC),
                decision=decision,
            )
            review_pull = ReviewPull(
                number=pull.number,
                issue_number=pull.issue_number,
                head_ref=pull.branch,
                repository=client.repository,
            )
            applied = apply_organization_review(
                candidate,
                review_comment,
                review_pull,
                schema_root=root,
                allow_registry_drift=True,
            )
            if applied.remaining_proposals and not applied.ready_for_finalization:
                raise AgentReviewError(
                    "review_preflight_failed",
                    applied.finalization_error or "机构审核结果无法安全落库",
                )
            proposal_directory = candidate / "proposals" / f"batch-issue-{pull.issue_number}"
            proposal_paths = sorted(proposal_directory.glob("*.json"))
            finalized = []
            if proposal_paths:
                finalized = finalize_proposal_set(
                    candidate,
                    proposal_paths,
                    moderator_github_user_id=1,
                )
            final_data = load_repository(candidate, validate=True, schema_root=root)
            organization_changes = _organization_changes(before_registry, final_data.registry)
            return {
                "ok": True,
                "checked_at": utc_now(),
                "main_sha": main_sha,
                "head_sha": pull.head_sha,
                "decision_sha256": canonical_json_sha256(decision),
                "remaining_proposals": applied.remaining_proposals,
                "mapped_proposals": applied.mapped_proposals,
                "rejected_proposals": applied.rejected_proposals,
                "created_organizations": applied.created_organizations,
                "updated_organizations": applied.updated_organizations,
                "organization_changes": organization_changes,
                "invalid_rows": applied.invalid_rows,
                "finalized_proposals": len(finalized),
            }
        except AgentReviewError:
            raise
        except RepositoryValidationError as error:
            message = error.issues[0] if error.issues else str(error)
            raise AgentReviewError("review_preflight_failed", message) from error
        except (OSError, subprocess.CalledProcessError, MentorDataError, ValueError) as error:
            raise AgentReviewError(
                "review_preflight_failed",
                str(error).splitlines()[0][:1_000],
            ) from error
        finally:
            client.runner(
                ["git", "worktree", "remove", "--force", str(candidate)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                cwd=root,
            )
            shutil.rmtree(candidate, ignore_errors=True)
