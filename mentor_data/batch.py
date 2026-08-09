from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import SubmissionError
from .github_events import (
    GitHubActor,
    GitHubIssueEvent,
    has_checked_confirmation,
    parse_issue_form,
    require_issue_trigger,
)
from .identifiers import proposed_mentor_id
from .io_utils import write_json_atomic
from .normalization import normalize_organization_key
from .proposals import (
    build_mentor_proposal,
    candidate_from_package_record,
    prepare_proposal_build_context,
)
from .repository import RepositoryData, load_repository
from .uploads import parse_community_package_rows

BATCH_FORM_LABELS = {
    "社区共享包": {"上传导师表格"},
    "补充说明": {"补充说明（选填）"},
    "投稿确认": set(),
}


@dataclass(frozen=True, slots=True)
class BatchProposalResult:
    paths: tuple[Path, ...]
    proposals: tuple[dict[str, Any], ...]
    invalid_rows: tuple[dict[str, Any], ...]

    @property
    def all_auto_eligible(self) -> bool:
        return (
            bool(self.proposals)
            and not self.invalid_rows
            and all(item["auto_eligible"] for item in self.proposals)
        )


def parse_batch_form(event: GitHubIssueEvent) -> dict[str, str]:
    sections = parse_issue_form(event.body, BATCH_FORM_LABELS)
    if not has_checked_confirmation(sections["投稿确认"]):
        raise SubmissionError("批量投稿确认未完成")
    return sections


def _pending_mentor(proposal: dict[str, Any]) -> dict[str, Any]:
    accepted = proposal["accepted"]
    affiliation_id = f"pending_affiliation_{proposal['issue']['batch_row']}"
    profiles = []
    if accepted.get("profile_url"):
        profiles.append({"url": accepted["profile_url"], "status": "current"})
    return {
        "id": proposed_mentor_id(proposal),
        "names": [{"value": accepted["name"], "is_primary": True}],
        "contacts": [
            {
                "normalized_value": accepted["email"],
                "status": "current",
                "is_primary": True,
                "affiliation_id": affiliation_id,
            }
        ],
        "affiliations": [
            {
                "id": affiliation_id,
                "organization_id": accepted.get("organization_id"),
                "status": "current",
                "is_primary": True,
            }
        ],
        "profiles": profiles,
    }


def _submitted_organization_path(proposal: dict[str, Any]) -> tuple[str, str, str]:
    submitted = proposal["submitted"]
    return tuple(
        normalize_organization_key(submitted.get(field))
        for field in (
            "submitted_university",
            "submitted_school",
            "submitted_department",
        )
    )


def create_batch_proposals(
    root: Path,
    event: GitHubIssueEvent,
    actor: GitHubActor,
    *,
    package_path: Path,
    output_directory: Path,
) -> BatchProposalResult:
    require_issue_trigger(event, expected_label="submission:batch")
    data: RepositoryData = load_repository(root, validate=True)
    parse_batch_form(event)
    package_rows = parse_community_package_rows(package_path, data.policy)
    if not package_rows:
        raise SubmissionError("批量共享包没有可投稿的数据行")

    output_directory.mkdir(parents=True, exist_ok=True)
    proposals: list[dict[str, Any]] = []
    paths: list[Path] = []
    invalid_rows: list[dict[str, Any]] = []
    build_context = prepare_proposal_build_context(data)
    pending_proposals_by_mentor_id: dict[str, dict[str, Any]] = {}
    for package_row in package_rows:
        record = package_row.record
        missing = [field for field in ("name", "email", "source_url") if not record[field]]
        if missing:
            invalid_rows.append(
                {
                    "batch_row": package_row.batch_row,
                    "sheet_row": package_row.sheet_row,
                    "reason_code": "missing_required_fields",
                    "message": f"缺少必填字段：{', '.join(missing)}",
                    "submitted": record,
                }
            )
            continue
        try:
            submitted, review_reasons = candidate_from_package_record(data, record)
            proposal = build_mentor_proposal(
                data,
                event,
                actor,
                submitted=submitted,
                review_reasons=review_reasons,
                proposal_id=f"proposal_issue_{event.number}_row_{package_row.batch_row}",
                batch_row=package_row.batch_row,
                context=build_context,
            )
            pending_target = pending_proposals_by_mentor_id.get(
                proposal.get("target_mentor_id")
            )
            same_resolved_organization = (
                isinstance(pending_target, dict)
                and isinstance(pending_target["accepted"].get("organization_id"), str)
                and pending_target["accepted"].get("organization_id")
                == proposal["accepted"].get("organization_id")
            )
            if (
                pending_target is not None
                and _submitted_organization_path(pending_target)
                != _submitted_organization_path(proposal)
                and not same_resolved_organization
            ):
                proposal["match_status"] = "conflict"
                proposal["review_reasons"] = list(
                    dict.fromkeys(
                        [
                            *proposal["review_reasons"],
                            "email_organization_conflict",
                            "identity_requires_manual_review",
                        ]
                    )
                )
                proposal["auto_eligible"] = False
        except SubmissionError as error:
            invalid_rows.append(
                {
                    "batch_row": package_row.batch_row,
                    "sheet_row": package_row.sheet_row,
                    "reason_code": "invalid_row_data",
                    "message": str(error).splitlines()[0][:500],
                    "submitted": record,
                }
            )
            continue
        path = output_directory / f"issue-{event.number}-row-{package_row.batch_row:04d}.json"
        write_json_atomic(path, proposal)
        proposals.append(proposal)
        paths.append(path)
        if proposal["target_mentor_id"] is None:
            pending_mentor = _pending_mentor(proposal)
            data.mentors.append(pending_mentor)
            build_context.register_mentor(pending_mentor)
            pending_proposals_by_mentor_id[pending_mentor["id"]] = proposal

    return BatchProposalResult(
        paths=tuple(paths),
        proposals=tuple(proposals),
        invalid_rows=tuple(invalid_rows),
    )
