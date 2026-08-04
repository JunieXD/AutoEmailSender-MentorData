from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import SubmissionError
from .github_events import GitHubActor, GitHubIssueEvent, parse_issue_form
from .identifiers import proposed_mentor_id
from .io_utils import write_json_atomic
from .proposals import (
    build_mentor_proposal,
    candidate_from_package_record,
    prepare_proposal_build_context,
)
from .repository import RepositoryData, load_repository
from .uploads import parse_community_package_rows

BATCH_FORM_LABELS = {"社区共享包", "补充说明", "投稿确认"}


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
    if "我确认" not in sections["投稿确认"]:
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


def create_batch_proposals(
    root: Path,
    event: GitHubIssueEvent,
    actor: GitHubActor,
    *,
    package_path: Path,
    output_directory: Path,
) -> BatchProposalResult:
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

    return BatchProposalResult(
        paths=tuple(paths),
        proposals=tuple(proposals),
        invalid_rows=tuple(invalid_rows),
    )
