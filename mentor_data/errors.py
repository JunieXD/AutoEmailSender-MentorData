from __future__ import annotations

from dataclasses import dataclass


class MentorDataError(Exception):
    """Base class for expected repository and submission errors."""


class RepositoryValidationError(MentorDataError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(issues))


class UnsafePackageError(MentorDataError):
    """Raised when a community upload violates package safety limits."""


class SubmissionError(MentorDataError):
    """Raised when a GitHub submission cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class ProposalFieldConflict:
    field: str
    message: str


class ProposalConflictError(SubmissionError):
    def __init__(self, conflicts: list[ProposalFieldConflict]) -> None:
        self.conflicts = tuple(conflicts)
        super().__init__("；".join(conflict.message for conflict in conflicts))


@dataclass(frozen=True, slots=True)
class ProposalFinalizationIssue:
    proposal_id: str
    batch_row: int | None
    name: str
    email: str
    field: str | None
    message: str


class ProposalSetValidationError(SubmissionError):
    FIELD_LABELS = {
        "name": "姓名",
        "email": "邮箱",
        "affiliations": "任职机构",
        "title": "职称",
        "research_directions": "研究方向",
        "recent_papers": "近期论文",
        "profile_url": "导师主页",
    }

    def __init__(self, issues: list[ProposalFinalizationIssue]) -> None:
        self.issues = tuple(issues)
        lines = [f"落库前发现 {len(issues)} 项导师数据冲突："]
        for issue in issues:
            location = (
                f"表格第 {issue.batch_row} 行"
                if issue.batch_row is not None
                else issue.proposal_id
            )
            field = self.FIELD_LABELS.get(issue.field or "", issue.field or "数据")
            lines.append(
                f"- {location} {issue.name}（{issue.email}）· {field}：{issue.message}"
            )
        super().__init__("\n".join(lines))
