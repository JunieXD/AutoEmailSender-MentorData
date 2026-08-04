from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import RepositoryValidationError, SubmissionError
from .internal_pulls import (
    SHA_PATTERN,
    InternalPull,
    load_internal_pull,
    materialize_proposal_paths,
    proposal_paths_from_diff,
)
from .io_utils import load_json, write_json_atomic
from .organization_review import (
    REVIEW_COMMENT_MARKER,
    TRUSTED_REVIEW_ASSOCIATIONS,
    ReviewComment,
    ReviewPull,
    apply_organization_review,
    load_review_comment,
)
from .proposals import finalize_proposal, finalize_proposal_set
from .reporting import finalize_report_proposal
from .repository import load_repository

ATTENTION_LABEL = "status:needs-attention"
PROMOTION_COMMENT_MARKER = "<!-- mentor-data-promotion-status:v1 -->"
REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)
TRUSTED_PERMISSIONS = {"admin", "maintain", "write"}


@dataclass(frozen=True, slots=True)
class Moderator:
    github_user_id: int
    github_login: str


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    kind: str
    issue_number: int
    pull_number: int
    base_sha: str
    proposal_commit_sha: str


@dataclass(frozen=True, slots=True)
class PromotionSummary:
    scanned: int
    merged: int
    failed: int
    skipped: int


class MainBranchMoved(RuntimeError):
    pass


class PromotionQueue:
    def __init__(
        self,
        *,
        root: Path,
        repository: str,
        include_attention: bool = False,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        if REPOSITORY_PATTERN.fullmatch(repository) is None:
            raise ValueError("GitHub 仓库名无效")
        self.root = root.resolve()
        self.repository = repository
        self.include_attention = include_attention
        self.runner = runner

    def run(self) -> PromotionSummary:
        pulls = self._list_open_pulls()
        merged = 0
        failed = 0
        skipped = 0
        for pull_payload in pulls:
            labels = {
                item.get("name")
                for item in pull_payload.get("labels", [])
                if isinstance(item, dict)
            }
            if ATTENTION_LABEL in labels and not self.include_attention:
                skipped += 1
                continue
            try:
                pull = load_internal_pull(
                    pull_payload,
                    expected_repository=self.repository,
                )
            except SubmissionError:
                skipped += 1
                continue
            try:
                if not self._is_ready(pull):
                    skipped += 1
                    continue
                self._promote(pull)
                self._remove_attention_label(pull.number)
                merged += 1
            except MainBranchMoved:
                skipped += 1
            except (RepositoryValidationError, SubmissionError, ValueError) as error:
                failed += 1
                try:
                    self._mark_attention(pull, str(error).splitlines()[0][:1_000])
                except (OSError, RuntimeError, subprocess.CalledProcessError) as mark_error:
                    print(
                        f"Could not record attention state for PR #{pull.number}: {mark_error}"
                    )
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                failed += 1
                print(f"Transient promotion failure for PR #{pull.number}: {error}")
        return PromotionSummary(
            scanned=len(pulls),
            merged=merged,
            failed=failed,
            skipped=skipped,
        )

    def _run(
        self,
        command: list[str],
        *,
        text: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        return self.runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            text=text,
            cwd=cwd,
        )

    def _gh_json(self, endpoint: str) -> Any:
        completed = self._run(["gh", "api", endpoint])
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("GitHub API 没有返回有效 JSON") from error

    def _gh_paginated_list(self, endpoint: str, *, max_items: int = 10_000) -> list[Any]:
        completed = self._run(["gh", "api", "--paginate", "--slurp", endpoint])
        try:
            pages = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("GitHub API 分页结果不是有效 JSON") from error
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise RuntimeError("GitHub API 分页结果格式无效")
        values = [item for page in pages for item in page]
        if len(values) > max_items:
            raise RuntimeError("GitHub API 分页结果超过安全上限")
        return values

    def _list_open_pulls(self) -> list[dict[str, Any]]:
        values = self._gh_paginated_list(
            f"repos/{self.repository}/pulls?state=open&per_page=100",
            max_items=999,
        )
        pulls: list[dict[str, Any]] = []
        for value in values:
            number = value.get("number") if isinstance(value, dict) else None
            if not isinstance(number, int) or number <= 0:
                raise RuntimeError("GitHub PR 列表包含无效编号")
            pulls.append(value)
        return sorted(pulls, key=lambda item: item["number"])

    def _is_ready(self, pull: InternalPull) -> bool:
        if not pull.draft:
            return True
        if pull.kind != "batch" or pull.status_label != "status:manual-review":
            return False
        return self._latest_batch_review_comment(pull, required=False) is not None

    def _fetch_commits(self, pull: InternalPull) -> None:
        self._run(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
                f"+refs/pull/{pull.number}/head:refs/remotes/origin/internal-pull-{pull.number}",
            ],
            cwd=self.root,
        )
        fetched = self._run(
            ["git", "rev-parse", f"refs/remotes/origin/internal-pull-{pull.number}"],
            cwd=self.root,
        ).stdout.strip()
        if fetched != pull.head_sha:
            raise MainBranchMoved("Pull Request 在处理前已经更新")

    def _origin_main_sha(self) -> str:
        value = self._run(["git", "rev-parse", "origin/main"], cwd=self.root).stdout.strip()
        if SHA_PATTERN.fullmatch(value) is None:
            raise RuntimeError("origin/main SHA 无效")
        return value

    @staticmethod
    def _receipt_path(pull: InternalPull) -> str:
        return f"reviews/promotions/issue-{pull.issue_number}.json"

    def _load_receipt(self, pull: InternalPull) -> PromotionReceipt | None:
        completed = self.runner(
            [
                "git",
                "-C",
                str(self.root),
                "show",
                f"{pull.head_sha}:{self._receipt_path(pull)}",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if completed.returncode != 0:
            return None
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SubmissionError("落库回执不是有效 JSON") from error
        expected = {
            "schema_version",
            "kind",
            "issue_number",
            "pull_number",
            "pull_url",
            "base_sha",
            "proposal_commit_sha",
            "finalized_at",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise SubmissionError("落库回执字段无效")
        if (
            value.get("schema_version") != 1
            or value.get("kind") != pull.kind
            or value.get("issue_number") != pull.issue_number
            or value.get("pull_number") != pull.number
            or value.get("pull_url") != pull.url
            or not isinstance(value.get("base_sha"), str)
            or SHA_PATTERN.fullmatch(value["base_sha"]) is None
            or not isinstance(value.get("proposal_commit_sha"), str)
            or SHA_PATTERN.fullmatch(value["proposal_commit_sha"]) is None
        ):
            raise SubmissionError("落库回执与 Pull Request 不一致")
        return PromotionReceipt(
            kind=pull.kind,
            issue_number=pull.issue_number,
            pull_number=pull.number,
            base_sha=value["base_sha"],
            proposal_commit_sha=value["proposal_commit_sha"],
        )

    def _promote(self, pull: InternalPull) -> None:
        self._fetch_commits(pull)
        main_sha = self._origin_main_sha()
        receipt = self._load_receipt(pull)
        if receipt is not None and receipt.base_sha == main_sha:
            self._validate_final_branch(pull, base_sha=main_sha)
            if pull.draft:
                self._run(["gh", "pr", "ready", str(pull.number), "--repo", self.repository])
            self._merge_pull(
                pull,
                expected_head_sha=pull.head_sha,
                expected_base_sha=main_sha,
            )
            return

        if receipt is None:
            proposal_source_sha = pull.head_sha
            proposal_base_sha = pull.base_sha
        else:
            proposal_source_sha = receipt.proposal_commit_sha
            proposal_base_sha = receipt.base_sha
        paths = proposal_paths_from_diff(
            self.root,
            pull,
            source_sha=proposal_source_sha,
            base_sha=proposal_base_sha,
            runner=self.runner,
        )
        with tempfile.TemporaryDirectory(prefix=f"mentor-data-pr-{pull.number}-") as temporary:
            candidate = Path(temporary) / "candidate"
            self._run(
                ["git", "worktree", "add", "--detach", str(candidate), main_sha],
                cwd=self.root,
            )
            try:
                materialize_proposal_paths(
                    self.root,
                    candidate,
                    source_sha=proposal_source_sha,
                    paths=paths,
                    runner=self.runner,
                )
                self._configure_git(candidate)
                self._run(["git", "add", "--", *paths], cwd=candidate)
                self._run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"queue: stage proposal for issue #{pull.issue_number}",
                    ],
                    cwd=candidate,
                )
                proposal_commit_sha = self._run(
                    ["git", "rev-parse", "HEAD"], cwd=candidate
                ).stdout.strip()
                self._finalize_candidate(candidate, pull)
                write_json_atomic(
                    candidate / self._receipt_path(pull),
                    {
                        "schema_version": 1,
                        "kind": pull.kind,
                        "issue_number": pull.issue_number,
                        "pull_number": pull.number,
                        "pull_url": pull.url,
                        "base_sha": main_sha,
                        "proposal_commit_sha": proposal_commit_sha,
                        "finalized_at": datetime.now(UTC)
                        .replace(microsecond=0)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                )
                load_repository(candidate, validate=True, schema_root=self.root)
                allowed_roots = (
                    "claims",
                    "records",
                    "registry",
                    "reports",
                    "proposals",
                    "reviews",
                )
                tracked = self._run(
                    ["git", "ls-files", "--", *allowed_roots],
                    cwd=candidate,
                ).stdout.splitlines()
                tracked_roots = {Path(path).parts[0] for path in tracked if Path(path).parts}
                stage_roots = [
                    path
                    for path in allowed_roots
                    if (candidate / path).exists() or path in tracked_roots
                ]
                self._run(
                    [
                        "git",
                        "add",
                        "-A",
                        "--",
                        *stage_roots,
                    ],
                    cwd=candidate,
                )
                self._run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"data: finalize moderation for issue #{pull.issue_number}",
                    ],
                    cwd=candidate,
                )
                final_sha = self._run(["git", "rev-parse", "HEAD"], cwd=candidate).stdout.strip()
                self._run(
                    [
                        "git",
                        "fetch",
                        "--no-tags",
                        "origin",
                        "+refs/heads/main:refs/remotes/origin/main",
                    ],
                    cwd=self.root,
                )
                if self._origin_main_sha() != main_sha:
                    raise MainBranchMoved("main 在落库期间已经更新")
                fresh = self._gh_json(f"repos/{self.repository}/pulls/{pull.number}")
                if not isinstance(fresh, dict) or fresh.get("head", {}).get("sha") != pull.head_sha:
                    raise MainBranchMoved("Pull Request 在落库期间已经更新")
                self._run(
                    [
                        "git",
                        "push",
                        f"--force-with-lease=refs/heads/{pull.branch}:{pull.head_sha}",
                        "origin",
                        f"HEAD:refs/heads/{pull.branch}",
                    ],
                    cwd=candidate,
                )
                if pull.draft:
                    self._run(
                        ["gh", "pr", "ready", str(pull.number), "--repo", self.repository]
                    )
                self._merge_pull(
                    pull,
                    expected_head_sha=final_sha,
                    expected_base_sha=main_sha,
                )
            finally:
                self.runner(
                    ["git", "worktree", "remove", "--force", str(candidate)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=self.root,
                )

    def _configure_git(self, candidate: Path) -> None:
        self._run(["git", "config", "user.name", "github-actions[bot]"], cwd=candidate)
        self._run(
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            cwd=candidate,
        )

    def _finalize_candidate(self, candidate: Path, pull: InternalPull) -> None:
        if pull.kind == "mentor":
            proposal_path = candidate / "proposals" / f"issue-{pull.issue_number}.json"
            proposal = load_json(proposal_path)
            automatic = (
                proposal.get("auto_eligible") is True
                and proposal.get("accepted") == proposal.get("submitted")
            )
            moderator = None if automatic else self._ready_moderator(pull)
            finalize_proposal(
                candidate,
                proposal_path,
                moderator_github_user_id=(moderator.github_user_id if moderator else None),
                schema_root=self.root,
            )
            proposal_path.unlink()
            return
        if pull.kind == "report":
            moderator = self._ready_moderator(pull)
            proposal_path = candidate / "reports" / "pending" / f"issue-{pull.issue_number}.json"
            finalize_report_proposal(
                candidate,
                proposal_path,
                moderator_github_user_id=moderator.github_user_id,
                moderator_github_login=moderator.github_login,
            )
            proposal_path.unlink()
            return

        proposal_directory = candidate / "proposals" / f"batch-issue-{pull.issue_number}"
        proposal_paths = sorted(proposal_directory.glob("*.json"))
        proposals = [load_json(path) for path in proposal_paths]
        automatic = bool(proposals) and all(
            proposal.get("auto_eligible") is True
            and proposal.get("accepted") == proposal.get("submitted")
            for proposal in proposals
        )
        moderator: Moderator | None = None
        if not automatic:
            review_comment = self._latest_batch_review_comment(pull, required=True)
            assert review_comment is not None
            moderator = Moderator(
                github_user_id=review_comment.reviewer_id,
                github_login=review_comment.reviewer_login,
            )
            self._require_collaborator(moderator)
            review_pull = ReviewPull(
                number=pull.number,
                issue_number=pull.issue_number,
                head_ref=pull.branch,
                repository=self.repository,
            )
            applied = apply_organization_review(
                candidate,
                review_comment,
                review_pull,
                schema_root=self.root,
                allow_registry_drift=True,
            )
            if not applied.ready_for_finalization and applied.remaining_proposals:
                raise SubmissionError(
                    applied.finalization_error or "机构审核结果暂时无法安全落库"
                )
            proposal_paths = sorted(proposal_directory.glob("*.json"))
        if proposal_paths:
            finalize_proposal_set(
                candidate,
                proposal_paths,
                moderator_github_user_id=(moderator.github_user_id if moderator else None),
            )
        shutil.rmtree(proposal_directory, ignore_errors=True)
        (candidate / "reviews" / "pending" / f"batch-issue-{pull.issue_number}.json").unlink(
            missing_ok=True
        )

    def _latest_batch_review_comment(
        self,
        pull: InternalPull,
        *,
        required: bool,
    ) -> ReviewComment | None:
        comments = self._gh_paginated_list(
            f"repos/{self.repository}/issues/{pull.number}/comments?per_page=100"
        )
        candidates = [
            item
            for item in comments
            if isinstance(item, dict)
            and str(item.get("body") or "").startswith(REVIEW_COMMENT_MARKER)
            and item.get("author_association") in TRUSTED_REVIEW_ASSOCIATIONS
        ]
        if not candidates:
            if required:
                raise SubmissionError("批量投稿尚未提交有效的机构审核结果")
            return None
        comment = candidates[-1]
        event = {"issue": {"number": pull.number, "pull_request": {}}, "comment": comment}
        with tempfile.TemporaryDirectory(prefix="mentor-data-review-comment-") as temporary:
            event_path = Path(temporary) / "event.json"
            event_path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
            return load_review_comment(self.root, event_path)

    def _ready_moderator(self, pull: InternalPull) -> Moderator:
        events = self._gh_paginated_list(
            f"repos/{self.repository}/issues/{pull.number}/timeline?per_page=100"
        )
        for event in reversed(events):
            actor = event.get("actor") if isinstance(event, dict) else None
            if (
                not isinstance(event, dict)
                or event.get("event") != "ready_for_review"
                or not isinstance(actor, dict)
            ):
                continue
            user_id = actor.get("id")
            login = actor.get("login")
            if (
                not isinstance(user_id, int)
                or user_id <= 0
                or not isinstance(login, str)
                or actor.get("type") != "User"
            ):
                continue
            moderator = Moderator(github_user_id=user_id, github_login=login)
            self._require_collaborator(moderator)
            return moderator
        raise SubmissionError("没有找到将 Draft 标记为 Ready 的受信任审核者")

    def _require_collaborator(self, moderator: Moderator) -> None:
        permission = self._gh_json(
            f"repos/{self.repository}/collaborators/{moderator.github_login}/permission"
        )
        if (
            not isinstance(permission, dict)
            or permission.get("permission") not in TRUSTED_PERMISSIONS
        ):
            raise SubmissionError("审核者当前没有仓库写入权限")
        user = permission.get("user")
        if not isinstance(user, dict) or user.get("id") != moderator.github_user_id:
            raise SubmissionError("审核者 GitHub 身份与权限查询结果不一致")

    def _validate_final_branch(self, pull: InternalPull, *, base_sha: str) -> None:
        completed = self._run(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                f"{base_sha}...{pull.head_sha}",
            ],
            cwd=self.root,
        )
        allowed_prefixes = (
            "claims/",
            "records/mentors/",
            "reports/resolutions/",
            "reviews/resolutions/",
            "reviews/promotions/",
        )
        for path in completed.stdout.splitlines():
            if path == "registry/organizations.yml" or path.startswith(allowed_prefixes):
                continue
            raise SubmissionError(f"已落库分支包含不允许的文件：{path}")
        with tempfile.TemporaryDirectory(prefix=f"mentor-data-final-{pull.number}-") as temporary:
            candidate = Path(temporary) / "candidate"
            self._run(
                ["git", "worktree", "add", "--detach", str(candidate), pull.head_sha],
                cwd=self.root,
            )
            try:
                load_repository(candidate, validate=True, schema_root=self.root)
            finally:
                self.runner(
                    ["git", "worktree", "remove", "--force", str(candidate)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=self.root,
                )

    def _merge_pull(
        self,
        pull: InternalPull,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
    ) -> None:
        self._run(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            ],
            cwd=self.root,
        )
        if self._origin_main_sha() != expected_base_sha:
            raise MainBranchMoved("main 在合并前已经更新")
        self._run(
            [
                "gh",
                "pr",
                "merge",
                str(pull.number),
                "--repo",
                self.repository,
                "--squash",
                "--delete-branch",
                "--match-head-commit",
                expected_head_sha,
            ]
        )

    def _mark_attention(self, pull: InternalPull, message: str) -> None:
        self._run(
            [
                "gh",
                "pr",
                "edit",
                str(pull.number),
                "--repo",
                self.repository,
                "--add-label",
                ATTENTION_LABEL,
            ]
        )
        body = (
            f"{PROMOTION_COMMENT_MARKER}\n"
            f"这条投稿暂时无法安全写入社区库：{message}\n\n"
            "修正 PR 后移除 `status:needs-attention` 标签，队列会继续处理。"
        )
        comments = self._gh_paginated_list(
            f"repos/{self.repository}/issues/{pull.number}/comments?per_page=100"
        )
        existing = next(
            (
                item
                for item in comments
                if isinstance(item, dict)
                and item.get("user", {}).get("login") == "github-actions[bot]"
                and str(item.get("body") or "").startswith(PROMOTION_COMMENT_MARKER)
            ),
            None,
        )
        if existing is None:
            self._run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{self.repository}/issues/{pull.number}/comments",
                    "-f",
                    f"body={body}",
                ]
            )
        else:
            comment_id = existing.get("id")
            if not isinstance(comment_id, int) or comment_id <= 0:
                raise RuntimeError("机器人状态评论 ID 无效")
            self._run(
                [
                    "gh",
                    "api",
                    "--method",
                    "PATCH",
                    f"repos/{self.repository}/issues/comments/{comment_id}",
                    "-f",
                    f"body={body}",
                ]
            )

    def _remove_attention_label(self, pull_number: int) -> None:
        self.runner(
            [
                "gh",
                "pr",
                "edit",
                str(pull_number),
                "--repo",
                self.repository,
                "--remove-label",
                ATTENTION_LABEL,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )


def write_github_outputs(path: Path, summary: PromotionSummary) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"scanned={summary.scanned}\n")
        handle.write(f"merged={summary.merged}\n")
        handle.write(f"failed={summary.failed}\n")
        handle.write(f"skipped={summary.skipped}\n")
        handle.write(f"publish={'true' if summary.merged else 'false'}\n")


def github_output_path(value: str | None = None) -> Path | None:
    resolved = value or os.environ.get("GITHUB_OUTPUT")
    return Path(resolved) if resolved else None
