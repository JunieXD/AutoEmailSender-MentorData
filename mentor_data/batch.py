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
)
from .repository import RepositoryData, load_repository
from .uploads import parse_community_package

BATCH_FORM_LABELS = {"社区共享包", "补充说明", "投稿确认"}


@dataclass(frozen=True, slots=True)
class BatchProposalResult:
    paths: tuple[Path, ...]
    proposals: tuple[dict[str, Any], ...]

    @property
    def all_auto_eligible(self) -> bool:
        return bool(self.proposals) and all(item["auto_eligible"] for item in self.proposals)


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
    records = parse_community_package(package_path, data.policy)
    if not records:
        raise SubmissionError("批量共享包没有可投稿的数据行")

    output_directory.mkdir(parents=True, exist_ok=True)
    proposals: list[dict[str, Any]] = []
    paths: list[Path] = []
    for batch_row, record in enumerate(records, start=1):
        submitted, review_reasons = candidate_from_package_record(data, record)
        proposal = build_mentor_proposal(
            data,
            event,
            actor,
            submitted=submitted,
            review_reasons=review_reasons,
            proposal_id=f"proposal_issue_{event.number}_row_{batch_row}",
            batch_row=batch_row,
        )
        path = output_directory / f"issue-{event.number}-row-{batch_row:04d}.json"
        write_json_atomic(path, proposal)
        proposals.append(proposal)
        paths.append(path)
        if proposal["target_mentor_id"] is None:
            data.mentors.append(_pending_mentor(proposal))

    return BatchProposalResult(paths=tuple(paths), proposals=tuple(proposals))
