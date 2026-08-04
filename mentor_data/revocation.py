from __future__ import annotations

import copy
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import SubmissionError
from .io_utils import write_json_atomic, write_yaml_atomic
from .normalization import normalized_https_url
from .repository import load_repository


def _remove_claim_support(mentor: dict[str, Any], revoked_claim_ids: set[str]) -> None:
    mentor["claim_ids"] = [
        claim_id for claim_id in mentor.get("claim_ids", []) if claim_id not in revoked_claim_ids
    ]
    for collection_name in ("names", "contacts", "affiliations", "profiles"):
        remaining: list[dict[str, Any]] = []
        for item in mentor.get(collection_name, []):
            item["claim_ids"] = [
                claim_id
                for claim_id in item.get("claim_ids", [])
                if claim_id not in revoked_claim_ids
            ]
            if item["claim_ids"] or item.get("resolution_ids"):
                remaining.append(item)
        mentor[collection_name] = remaining

    field_defaults: dict[str, Any] = {
        "title": None,
        "research_directions": [],
        "recent_papers": [],
    }
    for field, provenance in list(mentor.get("field_provenance", {}).items()):
        retained = [claim_id for claim_id in provenance if claim_id not in revoked_claim_ids]
        if retained:
            mentor["field_provenance"][field] = retained
            continue
        mentor["field_provenance"].pop(field, None)
        if field in field_defaults:
            mentor[field] = copy.deepcopy(field_defaults[field])


def _apply_revocation(
    root: Path,
    *,
    github_user_id: int,
    reason_code: str,
    source_issue_url: str | None,
    block_scopes: list[str],
) -> dict[str, Any]:
    data = load_repository(root, validate=True)
    target_claims = [
        claim for claim in data.claims if claim["contributor"]["github_user_id"] == github_user_id
    ]
    if not target_claims:
        raise SubmissionError(f"GitHub 用户 {github_user_id} 没有可撤销 Claim")
    claim_ids = {claim["id"] for claim in target_claims}
    affected_mentor_ids = sorted({claim["mentor_id"] for claim in target_claims})
    mentor_by_id = {mentor["id"]: mentor for mentor in data.mentors}
    removed_mentor_ids: list[str] = []
    updated_mentor_ids: list[str] = []

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    revocations = copy.deepcopy(data.revocations)
    events = revocations.setdefault("revocations", [])
    revocation_id = f"revocation_contributor_{github_user_id}_{now[:10].replace('-', '')}"
    if any(item.get("id") == revocation_id for item in events):
        raise SubmissionError(f"撤销事件 ID 已存在：{revocation_id}")

    for mentor_id in affected_mentor_ids:
        mentor_path = data.mentor_paths[mentor_id]
        mentor = copy.deepcopy(mentor_by_id[mentor_id])
        _remove_claim_support(mentor, claim_ids)
        if not mentor["claim_ids"]:
            mentor_path.unlink()
            removed_mentor_ids.append(mentor_id)
        else:
            has_primary_name = sum(1 for item in mentor["names"] if item.get("is_primary")) == 1
            has_primary_email = (
                sum(
                    1
                    for item in mentor["contacts"]
                    if item.get("status") == "current" and item.get("is_primary")
                )
                == 1
            )
            has_primary_affiliation = (
                sum(
                    1
                    for item in mentor["affiliations"]
                    if item.get("status") == "current" and item.get("is_primary")
                )
                == 1
            )
            if not (has_primary_name and has_primary_email and has_primary_affiliation):
                mentor["status"] = "disputed"
                mentor["status_reason"] = "贡献撤销后缺少独立身份来源"
                mentor["status_source_url"] = source_issue_url
                mentor["status_observed_at"] = now
            mentor["updated_at"] = now
            write_json_atomic(mentor_path, mentor)
            updated_mentor_ids.append(mentor_id)

    for claim in target_claims:
        data.claim_paths[claim["id"]].unlink()

    removed_proposal_ids: list[str] = []
    for proposal in data.proposals:
        if proposal.get("contributor", {}).get("github_user_id") != github_user_id:
            continue
        proposal_id = proposal["id"]
        data.proposal_paths[proposal_id].unlink()
        removed_proposal_ids.append(proposal_id)

    removed_report_proposal_ids: list[str] = []
    for proposal in data.report_proposals:
        reporter_id = proposal.get("reporter", {}).get("github_user_id")
        targets_removed_mentor = proposal.get("mentor_id") in removed_mentor_ids
        reporter_is_blocked = reporter_id == github_user_id and "report" in block_scopes
        if not targets_removed_mentor and not reporter_is_blocked:
            continue
        proposal_id = proposal["id"]
        data.report_proposal_paths[proposal_id].unlink()
        removed_report_proposal_ids.append(proposal_id)

    events.append(
        {
            "id": revocation_id,
            "kind": "contributor_revocation",
            "github_user_id": github_user_id,
            "claim_ids": sorted(claim_ids),
            "affected_mentor_ids": affected_mentor_ids,
            "removed_mentor_ids": removed_mentor_ids,
            "removed_proposal_ids": sorted(removed_proposal_ids),
            "removed_report_proposal_ids": sorted(removed_report_proposal_ids),
            "reason_code": reason_code,
            "source_issue_url": source_issue_url,
            "revoked_at": now,
        }
    )
    write_yaml_atomic(root / "registry" / "revocations.yml", revocations)

    if block_scopes:
        blocked = copy.deepcopy(data.blocked)
        entries = blocked.setdefault("blocked", [])
        existing = next(
            (item for item in entries if item.get("github_user_id") == github_user_id),
            None,
        )
        if existing is None:
            entries.append(
                {
                    "github_user_id": github_user_id,
                    "scopes": sorted(set(block_scopes)),
                    "blocked_at": now,
                    "reason_code": reason_code,
                }
            )
        else:
            existing["scopes"] = sorted(set(existing.get("scopes", [])) | set(block_scopes))
        write_yaml_atomic(root / "registry" / "blocked-contributors.yml", blocked)

    load_repository(root, validate=True)
    return {
        "revocation_id": revocation_id,
        "claim_ids": sorted(claim_ids),
        "updated_mentor_ids": updated_mentor_ids,
        "removed_mentor_ids": removed_mentor_ids,
        "removed_proposal_ids": sorted(removed_proposal_ids),
        "removed_report_proposal_ids": sorted(removed_report_proposal_ids),
    }


def revoke_contributor(
    root: Path,
    *,
    github_user_id: int,
    reason_code: str,
    source_issue_url: str | None,
    block_scopes: list[str],
    apply: bool,
) -> dict[str, Any]:
    if github_user_id <= 0:
        raise SubmissionError("github_user_id 必须是正整数")
    if not reason_code or not reason_code.replace("_", "").isalnum():
        raise SubmissionError("reason_code 只能包含字母、数字和下划线")
    if any(scope not in {"contribute", "report"} for scope in block_scopes):
        raise SubmissionError("block_scopes 只支持 contribute/report")
    if source_issue_url:
        normalized_source = normalized_https_url(source_issue_url)
        if normalized_source is None:
            raise SubmissionError("source_issue_url 必须是安全 HTTPS URL")
        source_issue_url = normalized_source

    resolved_root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="mentor-data-revoke-") as temporary:
        rehearsal_root = Path(temporary) / "repository"
        shutil.copytree(
            resolved_root,
            rehearsal_root,
            ignore=shutil.ignore_patterns(".git", ".venv", "dist", ".work", "__pycache__"),
        )
        preview = _apply_revocation(
            rehearsal_root,
            github_user_id=github_user_id,
            reason_code=reason_code,
            source_issue_url=source_issue_url,
            block_scopes=block_scopes,
        )
    if not apply:
        return {"dry_run": True, **preview}
    result = _apply_revocation(
        resolved_root,
        github_user_id=github_user_id,
        reason_code=reason_code,
        source_issue_url=source_issue_url,
        block_scopes=block_scopes,
    )
    return {"dry_run": False, **result}
