from __future__ import annotations


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
