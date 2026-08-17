from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .io_utils import load_json, write_json_atomic
from .normalization import (
    host_matches_domain,
    hostname_for_url,
    normalize_organization_key,
    normalize_text,
)
from .organization_review import REVIEW_COMMENT_MARKER

DRAFT_SCHEMA_VERSION = 1
COMMENT_CHARACTER_LIMIT = 65_536
LEVELS = ("university", "school", "department")
LEVEL_TYPES = {
    "university": {"university"},
    "school": {"school", "institute"},
    "department": {"department", "center", "laboratory"},
}
COMPACT_REVIEW_ENCODING = "shared_levels_v1"
AMBIGUOUS_SEPARATOR_PATTERN = re.compile(r"[/／\\|、,，;；]")
PARENTHESIZED_ORGANIZATION_PATTERN = re.compile(
    r"[（(【\[].*(?:学院|研究院|研究所|中心|实验室|系|部).*[）)】\]]"
)


class AgentReviewError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_command: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.next_command = next_command
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PullSnapshot:
    number: int
    issue_number: int
    title: str
    url: str
    branch: str
    head_sha: str
    base_sha: str
    draft: bool
    status_label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "issue_number": self.issue_number,
            "title": self.title,
            "url": self.url,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "draft": self.draft,
            "status_label": self.status_label,
        }


class ManifestOrganizations:
    def __init__(self, organizations: list[dict[str, Any]]) -> None:
        self.organizations = organizations
        self.by_id = {item["id"]: item for item in organizations}
        self._name_index: dict[tuple[str, str | None, str], list[str]] = {}
        for organization in organizations:
            level = organization_level(organization["type"])
            parent_id = organization.get("parent_id")
            for name in [organization["canonical_name"], *organization.get("aliases", [])]:
                key = normalize_organization_key(name)
                if key:
                    self._name_index.setdefault((level, parent_id, key), []).append(
                        organization["id"]
                    )

    def exact(self, level: str, parent_id: str | None, name: str) -> dict[str, Any] | None:
        ids = list(
            dict.fromkeys(
                self._name_index.get(
                    (level, parent_id, normalize_organization_key(name)),
                    [],
                )
            )
        )
        return self.by_id[ids[0]] if len(ids) == 1 else None

    def exact_candidates(
        self,
        level: str,
        parent_id: str | None,
        name: str,
    ) -> list[dict[str, Any]]:
        ids = list(
            dict.fromkeys(
                self._name_index.get(
                    (level, parent_id, normalize_organization_key(name)),
                    [],
                )
            )
        )
        return [self.by_id[item] for item in sorted(ids)]

    def university_id(self, organization_id: str) -> str | None:
        organization = self.by_id.get(organization_id)
        if organization is None:
            return None
        lineage_ids = organization.get("lineage_ids", [])
        for item_id in lineage_ids:
            candidate = self.by_id.get(item_id)
            if candidate and candidate.get("type") == "university":
                return item_id
        return organization_id if organization.get("type") == "university" else None

    def same_university_name(
        self,
        level: str,
        university_id: str,
        name: str,
        *,
        exclude_parent_id: str | None,
    ) -> list[dict[str, Any]]:
        name_key = normalize_organization_key(name)
        matches: list[dict[str, Any]] = []
        for organization in self.organizations:
            if organization_level(organization["type"]) != level:
                continue
            if organization.get("parent_id") == exclude_parent_id:
                continue
            if self.university_id(organization["id"]) != university_id:
                continue
            names = [organization["canonical_name"], *organization.get("aliases", [])]
            if name_key in {normalize_organization_key(item) for item in names}:
                matches.append(organization)
        return sorted(matches, key=lambda item: item["id"])

    def domains(self, organization_id: str) -> list[str]:
        domains: set[str] = set()
        seen: set[str] = set()
        current_id: str | None = organization_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            organization = self.by_id.get(current_id)
            if organization is None:
                break
            domains.update(organization.get("approved_domains", []))
            current_id = organization.get("parent_id")
        return sorted(domains)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def draft_path(workspace: Path, pull_number: int) -> Path:
    return workspace / f"pr-{pull_number}.json"


def manifest_cache_path(workspace: Path, pull_number: int) -> Path:
    return workspace / "cache" / f"pr-{pull_number}-manifest.json"


def pull_cache_path(workspace: Path, pull_number: int) -> Path:
    return workspace / "cache" / f"pr-{pull_number}-pull.json"


def load_draft(workspace: Path, pull_number: int) -> dict[str, Any]:
    path = draft_path(workspace, pull_number)
    if not path.is_file():
        raise AgentReviewError(
            "review_draft_missing",
            f"PR #{pull_number} 还没有审核底稿",
            next_command=f"mentor-data review plan --pr {pull_number}",
        )
    value = load_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != DRAFT_SCHEMA_VERSION:
        raise AgentReviewError(
            "review_draft_invalid",
            f"PR #{pull_number} 的审核底稿版本无效",
            next_command=f"mentor-data review plan --pr {pull_number} --reset",
        )
    return value


def save_draft(workspace: Path, draft: dict[str, Any]) -> None:
    write_json_atomic(draft_path(workspace, draft["pull"]["number"]), draft)


def cache_snapshot(
    workspace: Path,
    pull: PullSnapshot,
    manifest: dict[str, Any],
) -> None:
    write_json_atomic(pull_cache_path(workspace, pull.number), pull.as_dict())
    write_json_atomic(manifest_cache_path(workspace, pull.number), manifest)


def load_cached_manifest(workspace: Path, pull_number: int) -> dict[str, Any]:
    path = manifest_cache_path(workspace, pull_number)
    if not path.is_file():
        raise AgentReviewError(
            "review_cache_missing",
            f"PR #{pull_number} 的审核清单尚未缓存",
            next_command=f"mentor-data review inspect --pr {pull_number}",
        )
    value = load_json(path)
    if not isinstance(value, dict) or value.get("kind") != "batch_organization_review":
        raise AgentReviewError("review_manifest_invalid", "缓存的机构审核清单格式无效")
    return value


def assert_draft_current(
    draft: dict[str, Any],
    pull: PullSnapshot,
    manifest_sha256: str,
) -> None:
    expected = draft["pull"]
    if (
        expected.get("number") != pull.number
        or expected.get("head_sha") != pull.head_sha
        or draft.get("manifest_sha256") != manifest_sha256
    ):
        raise AgentReviewError(
            "review_draft_stale",
            f"PR #{pull.number} 已变化，旧底稿不能继续使用",
            next_command=f"mentor-data review plan --pr {pull.number} --reset",
        )


def organization_level(organization_type: str) -> str:
    if organization_type == "university":
        return "university"
    if organization_type in {"school", "institute"}:
        return "school"
    return "department"


def infer_organization_type(level: str, name: str) -> str | None:
    normalized = normalize_text(name)
    if level == "university":
        return "university"
    if level == "school":
        if normalized.endswith("研究院"):
            return "institute"
        if normalized.endswith("学院"):
            return "school"
        return None
    for suffix, organization_type in (
        ("实验室", "laboratory"),
        ("研究室", "laboratory"),
        ("中心", "center"),
        ("办公室", "department"),
        ("研究所", "department"),
        ("系", "department"),
        ("部", "department"),
    ):
        if normalized.endswith(suffix):
            return organization_type
    return None


def proposed_organization_id(
    organization_type: str,
    canonical_name: str,
    parent_id: str | None,
) -> str:
    seed = (
        f"{organization_type}\n"
        f"{normalize_organization_key(canonical_name)}\n"
        f"{parent_id or ''}"
    )
    return f"org_auto_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def group_path(group: dict[str, Any]) -> str:
    submitted = group["submitted"]
    return " / ".join(
        value
        for value in (
            submitted.get("university"),
            submitted.get("school"),
            submitted.get("department"),
        )
        if isinstance(value, str) and value
    )


def _question_id(pull_number: int, group_id: str, kind: str, subject: str) -> str:
    seed = f"{pull_number}\n{group_id}\n{kind}\n{subject}"
    return f"q_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _ambiguous_name(value: str) -> bool:
    normalized = normalize_text(value)
    return bool(
        AMBIGUOUS_SEPARATOR_PATTERN.search(normalized)
        or PARENTHESIZED_ORGANIZATION_PATTERN.search(normalized)
    )


def _source_root(group: dict[str, Any]) -> str | None:
    urls = group.get("source_urls", [])
    source_domains = set(group.get("source_domains", []))
    for url in urls:
        if hostname_for_url(url) not in source_domains:
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}/"
    return None


def university_domain_from_source(domain: str) -> str:
    """Collapse Chinese university subdomains to the institution-level edu.cn domain."""

    normalized = domain.casefold().rstrip(".")
    labels = normalized.split(".")
    if len(labels) > 3 and labels[-2:] == ["edu", "cn"]:
        return ".".join(labels[-3:])
    return normalized


def _university_source(group: dict[str, Any]) -> tuple[str | None, list[str]]:
    domains = sorted(
        {
            university_domain_from_source(item)
            for item in group.get("source_domains", [])
            if item
        }
    )
    if not domains:
        return _source_root(group), []
    preferred = domains[0]
    if preferred.endswith(".edu.cn"):
        return f"https://{preferred}/", domains
    return _source_root(group), domains


def _level_skip(level: str) -> dict[str, Any]:
    return {
        "level": level,
        "action": "skip",
        "organization_id": None,
        "organization_type": None,
        "canonical_name": None,
        "official_url": None,
        "approved_domains": [],
        "save_submitted_as_alias": False,
    }


def _level_existing(level: str, organization_id: str) -> dict[str, Any]:
    return {
        "level": level,
        "action": "existing",
        "organization_id": organization_id,
        "organization_type": None,
        "canonical_name": None,
        "official_url": None,
        "approved_domains": [],
        "save_submitted_as_alias": False,
    }


def _level_create(
    level: str,
    organization_type: str,
    canonical_name: str,
    group: dict[str, Any],
) -> dict[str, Any]:
    university_url, university_domains = _university_source(group)
    return {
        "level": level,
        "action": "create",
        "organization_id": None,
        "organization_type": organization_type,
        "canonical_name": normalize_text(canonical_name),
        "official_url": university_url if level == "university" else None,
        "approved_domains": university_domains if level == "university" else [],
        "save_submitted_as_alias": False,
    }


def _resolved_group_decision(
    group_id: str,
    *,
    levels: list[dict[str, Any]],
    target_organization_id: str | None = None,
    mapping_kind: str = "standard",
    mapping_reason: str | None = None,
    save_path_correction: bool = False,
) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "action": "resolve",
        "reason": None,
        "levels": levels,
        "target_organization_id": target_organization_id,
        "mapping_kind": mapping_kind,
        "mapping_reason": mapping_reason,
        "save_path_correction": save_path_correction,
        "row_overrides": [],
        "identity_resolutions": [],
    }


def _rejected_group_decision(group_id: str, reason: str) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "action": "reject",
        "reason": reason,
        "levels": [],
        "target_organization_id": None,
        "mapping_kind": "standard",
        "mapping_reason": None,
        "save_path_correction": False,
        "row_overrides": [],
        "identity_resolutions": [],
    }


def _mapping_kind_for_target(
    submitted_department: str | None,
    organization_type: str | None,
) -> str:
    if (
        submitted_department
        and submitted_department.endswith("学院")
        and organization_type == "school"
    ):
        return "department_as_school"
    if (
        submitted_department
        and submitted_department.endswith("研究院")
        and organization_type == "institute"
    ):
        return "department_as_institute"
    return "custom"


def _group_question(
    pull_number: int,
    group: dict[str, Any],
    *,
    kind: str,
    subject: str,
    level: str | None,
    prompt: str,
    reason: str,
    rule_default: str | None,
    options: list[dict[str, Any]],
    context: dict[str, Any],
    answers: dict[str, Any],
    context_recommendation: str | None = None,
    recommendation_confidence: str | None = None,
    path_correction_choices: tuple[str, ...] = (),
) -> dict[str, Any]:
    question_id = _question_id(pull_number, group["id"], kind, subject)
    answer = copy.deepcopy(answers.get(question_id))
    return {
        "id": question_id,
        "group_id": group["id"],
        "type": kind,
        "level": level,
        "path": group_path(group),
        "prompt": prompt,
        "reason": reason,
        "rule_default": rule_default,
        "context_recommendation": context_recommendation,
        "recommendation_confidence": recommendation_confidence,
        "path_correction_scopes": (
            ["current-batch", "future-identical-path"]
            if path_correction_choices
            else []
        ),
        "path_correction_choices": list(path_correction_choices),
        "options": options,
        "context": context,
        "status": "answered" if answer is not None else "pending",
        "answer": answer,
    }


def _answer_choice(question: dict[str, Any]) -> str | None:
    answer = question.get("answer")
    return answer.get("choice") if isinstance(answer, dict) else None


def _organization_matches_sources(
    group: dict[str, Any],
    organizations: ManifestOrganizations,
    organization_id: str,
) -> bool:
    domains = organizations.domains(organization_id)
    return bool(domains) and all(
        any(host_matches_domain(source_domain, domain) for domain in domains)
        for source_domain in group.get("source_domains", [])
    )


def _question_options(
    *values: tuple[str, str, list[str]] | tuple[str, str, list[str], list[str]],
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for item in values:
        value, label, requires = item[:3]
        option: dict[str, Any] = {
            "value": value,
            "label": label,
            "requires": requires,
        }
        if len(item) == 4:
            option["optional"] = item[3]
        options.append(option)
    return options


def _department_name_without_parent(name: str, parent_name: str) -> str:
    normalized_name = normalize_organization_key(name)
    normalized_parent = normalize_organization_key(parent_name)
    if normalized_parent and normalized_name.startswith(normalized_parent):
        return normalized_name[len(normalized_parent) :]
    return normalized_name


def _names_are_similar(first: str, second: str, parent_name: str) -> bool:
    first_key = _department_name_without_parent(first, parent_name)
    second_key = _department_name_without_parent(second, parent_name)
    if not first_key or not second_key or first_key == second_key:
        return bool(first_key and second_key)
    if first_key in second_key or second_key in first_key:
        return True
    return difflib.SequenceMatcher(None, first_key, second_key).ratio() >= 0.78


def _source_directory(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.casefold()}://{parsed.hostname.casefold()}{port}{path}"


def _source_url_key(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    path = parsed.path or "/"
    filename = path.rstrip("/").rsplit("/", 1)[-1].casefold()
    if not filename or re.fullmatch(
        r"(?:index|list|default)(?:[_-]\d+)?\.(?:s?html?|php|aspx?)",
        filename,
    ):
        return None
    port = f":{parsed.port}" if parsed.port else ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.casefold()}://{parsed.hostname.casefold()}{port}{path}{query}"


def _candidate_group_context(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_id": group["id"],
        "path": group_path(group),
        "name": normalize_text(group["submitted"].get("department")),
        "row_count": len(group.get("rows", [])),
    }


def _similar_new_department_contexts(groups: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return stable review contexts for potential new sibling collisions."""

    candidates = [
        group
        for group in groups
        if infer_organization_type(
            "department", normalize_text(group["submitted"].get("department"))
        )
        in LEVEL_TYPES["department"]
    ]
    candidates_by_id = {group["id"]: group for group in candidates}
    adjacency: dict[str, set[str]] = {group["id"]: set() for group in candidates}
    relation_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for index, first in enumerate(candidates):
        first_submitted = first["submitted"]
        for second in candidates[index + 1 :]:
            second_submitted = second["submitted"]
            same_parent = all(
                normalize_organization_key(first_submitted.get(level))
                == normalize_organization_key(second_submitted.get(level))
                for level in ("university", "school")
            )
            if not same_parent:
                continue
            first_name = normalize_text(first_submitted.get("department"))
            second_name = normalize_text(second_submitted.get("department"))
            parent_name = normalize_text(first_submitted.get("school"))
            first_directories = {
                directory
                for url in first.get("source_urls", [])
                if (directory := _source_directory(url)) is not None
            }
            second_directories = {
                directory
                for url in second.get("source_urls", [])
                if (directory := _source_directory(url)) is not None
            }
            shared_directories = sorted(
                first_directories.intersection(second_directories)
            )
            first_source_urls = {
                key
                for url in first.get("source_urls", [])
                if (key := _source_url_key(url)) is not None
            }
            second_source_urls = {
                key
                for url in second.get("source_urls", [])
                if (key := _source_url_key(url)) is not None
            }
            shared_source_urls = sorted(first_source_urls.intersection(second_source_urls))
            similar_name = _names_are_similar(first_name, second_name, parent_name)
            if not similar_name and not shared_source_urls:
                continue
            pair = tuple(sorted((first["id"], second["id"])))
            adjacency[first["id"]].add(second["id"])
            adjacency[second["id"]].add(first["id"])
            relation_evidence[pair] = {
                "shared_source_directories": shared_directories[:3],
                "shared_source_urls": shared_source_urls[:3],
                "evidence": [
                    value
                    for value, enabled in (
                        ("similar_name", similar_name),
                        ("shared_source_directory", bool(shared_directories)),
                        ("shared_source_url", bool(shared_source_urls)),
                    )
                    if enabled
                ],
            }

    result: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()
    for candidate_id in sorted(candidates_by_id):
        if candidate_id in visited or not adjacency[candidate_id]:
            continue
        component: set[str] = set()
        pending = [candidate_id]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(sorted(adjacency[current] - component, reverse=True))
        visited.update(component)
        component_groups = [candidates_by_id[group_id] for group_id in component]
        parent_name = normalize_text(component_groups[0]["submitted"].get("school"))
        component_anchor = min(
            component_groups,
            key=lambda group: (
                len(
                    _department_name_without_parent(
                        normalize_text(group["submitted"].get("department")),
                        parent_name,
                    )
                ),
                len(normalize_text(group["submitted"].get("department"))),
                normalize_text(group["submitted"].get("department")),
                group["id"],
            ),
        )
        for reviewed in component_groups:
            direct_name_ids = {reviewed["id"]}
            for pair, relation in relation_evidence.items():
                if reviewed["id"] not in pair or "similar_name" not in relation["evidence"]:
                    continue
                direct_name_ids.update(pair)
            direct_name_groups = [candidates_by_id[group_id] for group_id in direct_name_ids]
            recommended_group: dict[str, Any] | None = None
            if len(direct_name_groups) > 1:
                local_anchor = min(
                    direct_name_groups,
                    key=lambda group: (
                        len(
                            _department_name_without_parent(
                                normalize_text(group["submitted"].get("department")),
                                parent_name,
                            )
                        ),
                        len(normalize_text(group["submitted"].get("department"))),
                        normalize_text(group["submitted"].get("department")),
                        group["id"],
                    ),
                )
                if reviewed["id"] == local_anchor["id"]:
                    continue
                recommended_group = local_anchor
                context_ids = direct_name_ids
            else:
                if reviewed["id"] == component_anchor["id"]:
                    continue
                context_ids = component

            context_relations = [
                value
                for pair, value in relation_evidence.items()
                if pair[0] in context_ids and pair[1] in context_ids
            ]
            evidence = sorted(
                {item for relation in context_relations for item in relation["evidence"]}
            )
            shared_directories = sorted(
                {
                    item
                    for relation in context_relations
                    for item in relation["shared_source_directories"]
                }
            )
            shared_source_urls = sorted(
                {item for relation in context_relations for item in relation["shared_source_urls"]}
            )
            candidate_contexts = sorted(
                (_candidate_group_context(candidates_by_id[group_id]) for group_id in context_ids),
                key=lambda item: (item["name"], item["group_id"]),
            )
            reviewed_name = normalize_text(reviewed["submitted"].get("department"))
            recommended_name = (
                normalize_text(recommended_group["submitted"].get("department"))
                if recommended_group is not None
                else ""
            )
            exact_parent_cleanup = _department_name_without_parent(
                reviewed_name, parent_name
            ) == _department_name_without_parent(recommended_name, parent_name)
            # Fuzzy similarity is useful for surfacing a collision, but only an exact
            # parent-prefix cleanup is strong enough to recommend a canonical target.
            has_safe_recommendation = recommended_group is not None and exact_parent_cleanup
            result[reviewed["id"]] = {
                "candidate_names": [item["name"] for item in candidate_contexts],
                "candidate_groups": candidate_contexts,
                "recommended_canonical_group_id": (
                    recommended_group["id"] if has_safe_recommendation else None
                ),
                "recommended_canonical_name": (
                    recommended_name if has_safe_recommendation else None
                ),
                "recommendation_confidence": "high" if has_safe_recommendation else None,
                "shared_source_directories": shared_directories[:3],
                "shared_source_urls": shared_source_urls[:3],
                "evidence": evidence,
            }
    return result


def _clear_planned_department(
    group: dict[str, Any],
    organizations: ManifestOrganizations,
) -> dict[str, Any] | None:
    """Resolve the stable ID for an unambiguous department creation in this batch."""

    submitted = group["submitted"]
    parent_id: str | None = None
    parent_name = ""
    university_name = normalize_text(submitted.get("university"))
    for level in ("university", "school"):
        value = normalize_text(submitted.get(level))
        if not value or _ambiguous_name(value):
            return None
        candidates = organizations.exact_candidates(level, parent_id, value)
        if len(candidates) == 1:
            organization = candidates[0]
            parent_id = organization["id"]
            parent_name = organization["canonical_name"]
            continue
        if candidates:
            return None
        organization_type = infer_organization_type(level, value)
        if organization_type not in LEVEL_TYPES[level]:
            return None
        parent_id = proposed_organization_id(organization_type, value, parent_id)
        parent_name = value

    value = normalize_text(submitted.get("department"))
    organization_type = infer_organization_type("department", value)
    if (
        not value
        or _ambiguous_name(value)
        or organization_type not in LEVEL_TYPES["department"]
        or value.endswith(("学院", "研究院"))
        or normalize_organization_key(value)
        in {
            normalize_organization_key(parent_name),
            normalize_organization_key(university_name),
        }
    ):
        return None
    if organizations.exact_candidates("department", parent_id, value):
        return None
    return {
        "group_id": group["id"],
        "path": group_path(group),
        "name": value,
        "parent_id": parent_id,
        "organization_id": proposed_organization_id(
            organization_type,
            value,
            parent_id,
        ),
        "organization_type": organization_type,
        "row_count": len(group.get("rows", [])),
    }


def _planned_cross_parent_contexts(
    groups: list[dict[str, Any]],
    organizations: ManifestOrganizations,
) -> dict[str, dict[str, Any]]:
    """Find exact same-name department creations under different planned parents."""

    candidates = [
        candidate
        for group in groups
        if (candidate := _clear_planned_department(group, organizations)) is not None
    ]
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    submitted_by_id = {group["id"]: group["submitted"] for group in groups}
    for candidate in candidates:
        submitted = submitted_by_id[candidate["group_id"]]
        key = (
            normalize_organization_key(submitted.get("university")),
            normalize_organization_key(candidate["name"]),
        )
        buckets.setdefault(key, []).append(candidate)

    result: dict[str, dict[str, Any]] = {}
    for bucket in buckets.values():
        if len({item["parent_id"] for item in bucket}) < 2:
            continue
        for candidate in bucket:
            alternatives = sorted(
                (
                    copy.deepcopy(item)
                    for item in bucket
                    if item["parent_id"] != candidate["parent_id"]
                ),
                key=lambda item: (item["path"], item["group_id"]),
            )
            if alternatives:
                result[candidate["group_id"]] = {"candidates": alternatives}
    return result


def _plan_path(
    pull_number: int,
    group: dict[str, Any],
    organizations: ManifestOrganizations,
    answers: dict[str, Any],
    similar_new_department: dict[str, Any] | None = None,
    planned_cross_parent: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    questions: list[dict[str, Any]] = []
    rules: list[str] = []
    creations: list[dict[str, Any]] = []
    suggestion = group.get("suggested_path_correction")
    if (
        isinstance(suggestion, dict)
        and suggestion.get("source") == "history"
        and isinstance(suggestion.get("target_organization_id"), str)
    ):
        target_id = suggestion["target_organization_id"]
        target = organizations.by_id.get(target_id)
        if target is not None and _organization_matches_sources(
            group,
            organizations,
            target_id,
        ):
            rules.append("historical_path_correction")
            decision = _resolved_group_decision(
                group["id"],
                levels=[],
                target_organization_id=target_id,
                mapping_kind=suggestion["kind"],
                mapping_reason=suggestion["reason"],
                save_path_correction=False,
            )
            return decision, questions, rules, creations

    submitted = group["submitted"]
    levels: list[dict[str, Any]] = []
    parent_id: str | None = None
    parent_name: str | None = None
    university_level: dict[str, Any] | None = None
    final_target_id: str | None = None
    final_target_is_new = False
    saved_canonical_path_correction = False
    canonical_path_correction_reason: str | None = None

    for index, level in enumerate(LEVELS):
        value = normalize_text(submitted.get(level))
        if not value:
            if level == "university":
                raise AgentReviewError(
                    "review_manifest_invalid",
                    f"分组 {group['id']} 缺少学校名称",
                )
            levels.append(_level_skip(level))
            rules.append(f"empty_{level}")
            if level == "school" and normalize_text(submitted.get("department")):
                question = _group_question(
                    pull_number,
                    group,
                    kind="missing_parent_level",
                    subject=level,
                    level=level,
                    prompt="学院为空但系所不为空，最终机构层级需要确认",
                    reason="现有机构 Schema 不允许跳过学院后继续按标准路径创建系所。",
                    rule_default=None,
                    path_correction_choices=("map-existing", "create-under-university"),
                    options=_question_options(
                        ("map-existing", "直接映射到现有机构", ["organization_id"]),
                        (
                            "create-under-university",
                            "在学校下新建机构",
                            ["organization_type"],
                            ["canonical_name", "official_url", "approved_domains"],
                        ),
                        ("reject-group", "不收录这一组", []),
                    ),
                    context={"submitted": copy.deepcopy(submitted)},
                    answers=answers,
                )
                questions.append(question)
                choice = _answer_choice(question)
                if choice is None:
                    return None, questions, rules, creations
                answer = question["answer"]
                if choice == "reject-group":
                    return (
                        _rejected_group_decision(
                            group["id"],
                            answer.get("reason") or "机构层级无法确定",
                        ),
                        questions,
                        rules,
                        creations,
                    )
                if choice == "map-existing":
                    target_id = answer.get("organization_id")
                    target = organizations.by_id.get(target_id)
                    if target is None:
                        raise AgentReviewError("review_answer_invalid", "选择的现有机构不存在")
                    return (
                        _resolved_group_decision(
                            group["id"],
                            levels=[],
                            target_organization_id=target_id,
                            mapping_kind="custom",
                            mapping_reason=answer.get("reason") or "学院字段为空，人工确认最终机构",
                            save_path_correction=bool(answer.get("save_path_correction")),
                        ),
                        questions,
                        rules,
                        creations,
                    )
                organization_type = answer.get("organization_type")
                canonical_name = answer.get("canonical_name") or submitted["department"]
                if organization_type not in {
                    "school",
                    "institute",
                    "department",
                    "center",
                    "laboratory",
                }:
                    raise AgentReviewError("review_answer_invalid", "新机构类型无效")
                organization_id = proposed_organization_id(
                    organization_type,
                    canonical_name,
                    parent_id,
                )
                creations.append(
                    {
                        "organization_id": organization_id,
                        "organization_type": organization_type,
                        "canonical_name": canonical_name,
                        "parent_id": parent_id,
                        "official_url": answer.get("official_url"),
                        "approved_domains": answer.get("approved_domains", []),
                    }
                )
                base_levels = [
                    copy.deepcopy(university_level)
                    if university_level
                    else _level_skip("university"),
                    _level_skip("school"),
                    _level_skip("department"),
                ]
                return (
                    _resolved_group_decision(
                        group["id"],
                        levels=base_levels,
                        target_organization_id=organization_id,
                        mapping_kind="custom",
                        mapping_reason=answer.get("reason") or "学院字段为空，人工确认机构层级",
                        save_path_correction=bool(answer.get("save_path_correction")),
                    ),
                    questions,
                    rules,
                    creations,
                )
            continue

        if level == "department":
            value_key = normalize_organization_key(value)
            parent_key = normalize_organization_key(parent_name)
            university_key = normalize_organization_key(submitted.get("university"))
            if value_key == parent_key:
                levels.append(_level_skip(level))
                rules.append("repeated_parent_name")
                continue
            if value_key == university_key:
                if university_level is None:
                    raise AgentReviewError(
                        "review_manifest_invalid",
                        f"分组 {group['id']} 无法确定重复学校字段的目标",
                    )
                levels = [
                    copy.deepcopy(university_level),
                    _level_skip("school"),
                    _level_skip("department"),
                ]
                if university_level["action"] == "existing":
                    final_target_id = university_level["organization_id"]
                    final_target_is_new = False
                else:
                    final_target_id = proposed_organization_id(
                        "university",
                        university_level["canonical_name"],
                        None,
                    )
                    final_target_is_new = True
                rules.append("repeated_university_name")
                break

        candidates = organizations.exact_candidates(level, parent_id, value)
        if len(candidates) == 1:
            organization = candidates[0]
            current_level = _level_existing(level, organization["id"])
            levels.append(current_level)
            if level == "university":
                university_level = current_level
            parent_id = organization["id"]
            parent_name = organization["canonical_name"]
            final_target_id = organization["id"]
            final_target_is_new = False
            rules.append(f"exact_{level}_match")
            continue

        university_id: str | None = None
        if university_level and university_level["action"] == "existing":
            university_id = university_level["organization_id"]
        elif university_level and university_level["action"] == "create":
            university_id = proposed_organization_id(
                "university",
                university_level["canonical_name"],
                None,
            )
        cross_parent_candidates = (
            organizations.same_university_name(
                level,
                university_id,
                value,
                exclude_parent_id=parent_id,
            )
            if level == "department" and university_id is not None and not candidates
            else []
        )
        if cross_parent_candidates:
            proposed_type = infer_organization_type(level, value)
            proposed_id = (
                proposed_organization_id(proposed_type, value, parent_id)
                if proposed_type in LEVEL_TYPES[level]
                else None
            )
            question = _group_question(
                pull_number,
                group,
                kind="same_name_different_parent",
                subject="|".join(item["id"] for item in cross_parent_candidates),
                level=level,
                prompt=f"同校其他父级下已有同名机构“{value}”，请确认当前归属",
                reason="同名机构可能是跨单位放置，也可能应映射到已有机构。",
                rule_default="keep-placement",
                path_correction_choices=("map-existing",),
                options=_question_options(
                    ("keep-placement", "保留投稿中的当前父级归属", []),
                    ("map-existing", "映射到同校已有机构", ["organization_id"]),
                    ("use-parent", "忽略当前字段并归入当前父级", []),
                    ("reject-group", "不收录这一组", []),
                ),
                context={
                    "submitted": copy.deepcopy(submitted),
                    "current_parent_id": parent_id,
                    "candidates": [
                        {
                            "id": item["id"],
                            "type": item["type"],
                            "path": " / ".join(item.get("lineage_names", [])),
                            "parent_id": item.get("parent_id"),
                        }
                        for item in cross_parent_candidates
                    ],
                    "conflict_pair_ids": (
                        [
                            sorted((proposed_id, item["id"]))
                            for item in cross_parent_candidates
                        ]
                        if proposed_id is not None
                        else []
                    ),
                },
                answers=answers,
            )
            questions.append(question)
            choice = _answer_choice(question)
            if choice is None:
                return None, questions, rules, creations
            answer = question["answer"]
            if choice == "reject-group":
                return (
                    _rejected_group_decision(
                        group["id"],
                        answer.get("reason") or "同校跨父级机构归属无法确定",
                    ),
                    questions,
                    rules,
                    creations,
                )
            if choice == "use-parent":
                levels.append(_level_skip(level))
                rules.append("user_use_parent_for_cross_parent_name")
                break
            if choice == "map-existing":
                target_id = answer.get("organization_id")
                if target_id not in {item["id"] for item in cross_parent_candidates}:
                    raise AgentReviewError(
                        "review_answer_invalid",
                        "所选机构不是当前同校跨父级候选",
                    )
                return (
                    _resolved_group_decision(
                        group["id"],
                        levels=[],
                        target_organization_id=target_id,
                        mapping_kind="custom",
                        mapping_reason=answer.get("reason")
                        or "人工确认同名机构应映射到同校已有归属",
                        save_path_correction=bool(answer.get("save_path_correction")),
                    ),
                    questions,
                    rules,
                    creations,
                )
            if choice != "keep-placement":
                raise AgentReviewError("review_answer_invalid", "同校跨父级机构选择无效")
            rules.append("user_keep_cross_parent_placement")

        if planned_cross_parent:
            planned_candidates = planned_cross_parent.get("candidates", [])
            proposed_type = infer_organization_type(level, value)
            proposed_id = proposed_organization_id(
                proposed_type,
                value,
                parent_id,
            )
            question = _group_question(
                pull_number,
                group,
                kind="same_name_different_parent",
                subject="planned|" + "|".join(
                    item["group_id"] for item in planned_candidates
                ),
                level=level,
                prompt=f"本批次其他父级下也计划新建同名机构“{value}”，请确认当前归属",
                reason="同名机构可能是跨单位分别设置，也可能应统一归入其中一个父级。",
                rule_default="keep-placement",
                path_correction_choices=("map-planned",),
                options=_question_options(
                    ("keep-placement", "保留投稿中的当前父级归属", []),
                    ("map-planned", "映射到本批次另一计划机构", ["organization_id"]),
                    ("use-parent", "忽略当前字段并归入当前父级", []),
                    ("reject-group", "不收录这一组", []),
                ),
                context={
                    "submitted": copy.deepcopy(submitted),
                    "current_parent_id": parent_id,
                    "candidates": copy.deepcopy(planned_candidates),
                    "conflict_pair_ids": [
                        sorted((proposed_id, item["organization_id"]))
                        for item in planned_candidates
                    ],
                },
                answers=answers,
            )
            questions.append(question)
            choice = _answer_choice(question)
            if choice is None:
                return None, questions, rules, creations
            answer = question["answer"]
            if choice == "reject-group":
                return (
                    _rejected_group_decision(
                        group["id"],
                        answer.get("reason") or "本批次跨父级同名机构归属无法确定",
                    ),
                    questions,
                    rules,
                    creations,
                )
            if choice == "use-parent":
                levels.append(_level_skip(level))
                rules.append("user_use_parent_for_cross_parent_name")
                break
            if choice == "map-planned":
                target_id = answer.get("organization_id")
                if target_id not in {
                    item["organization_id"] for item in planned_candidates
                }:
                    raise AgentReviewError(
                        "review_answer_invalid",
                        "所选机构不是本批次的跨父级同名候选",
                    )
                return (
                    _resolved_group_decision(
                        group["id"],
                        levels=[],
                        target_organization_id=target_id,
                        mapping_kind="custom",
                        mapping_reason=answer.get("reason")
                        or "人工确认同名机构应映射到本批次另一计划归属",
                        save_path_correction=bool(answer.get("save_path_correction")),
                    ),
                    questions,
                    rules,
                    creations,
                )
            if choice != "keep-placement":
                raise AgentReviewError("review_answer_invalid", "本批次跨父级机构选择无效")
            rules.append("user_keep_cross_parent_placement")

        if len(candidates) > 1:
            ambiguity_reason = "同一父级下存在多个精确名称候选。"
        elif _ambiguous_name(value):
            ambiguity_reason = "名称包含并列或括号机构，可能表示别名、合署或多个机构。"
        else:
            ambiguity_reason = "名称和层级不能由现有机构树唯一确定。"

        inferred_type = infer_organization_type(level, value)
        misplaced_school_level = level == "department" and (
            value.endswith("学院") or value.endswith("研究院")
        )
        automatic_creation = (
            not candidates
            and not _ambiguous_name(value)
            and inferred_type is not None
            and not misplaced_school_level
        )
        if automatic_creation and level == "department" and similar_new_department:
            recommended_name = similar_new_department.get("recommended_canonical_name")
            question = _group_question(
                pull_number,
                group,
                kind="similar_new_sibling",
                subject="|".join(
                    item["group_id"]
                    for item in similar_new_department.get("candidate_groups", [])
                )
                or "|".join(similar_new_department["candidate_names"]),
                level=level,
                prompt="同一父级下存在疑似重复的新机构，请确认是否合并名称",
                reason="新机构名称相似、存在包含关系或共享同一来源页，不能自动拆分。",
                rule_default="keep-separate",
                context_recommendation="use-canonical" if recommended_name else None,
                recommendation_confidence=(
                    similar_new_department.get("recommendation_confidence")
                    if recommended_name
                    else None
                ),
                options=_question_options(
                    ("use-canonical", "合并为一个规范名称", ["canonical_name"]),
                    ("keep-separate", "确认是两个不同机构", []),
                    ("use-parent", "不创建系所并归入当前学院", []),
                    ("reject-group", "不收录这一组", []),
                ),
                context=copy.deepcopy(similar_new_department),
                answers=answers,
                path_correction_choices=("use-canonical",),
            )
            questions.append(question)
            choice = _answer_choice(question)
            if choice is None:
                return None, questions, rules, creations
            answer = question["answer"]
            if choice == "reject-group":
                return (
                    _rejected_group_decision(
                        group["id"], answer.get("reason") or "新机构关系无法确定"
                    ),
                    questions,
                    rules,
                    creations,
                )
            if choice == "use-parent":
                levels.append(_level_skip(level))
                rules.append("user_use_parent_for_similar_sibling")
                break
            if choice == "use-canonical":
                submitted_value = value
                value = normalize_text(answer.get("canonical_name"))
                if not value:
                    raise AgentReviewError("review_answer_invalid", "合并机构必须填写规范名称")
                inferred_type = infer_organization_type(level, value)
                if inferred_type not in LEVEL_TYPES[level]:
                    raise AgentReviewError("review_answer_invalid", "规范名称与机构层级不匹配")
                if answer.get("save_path_correction"):
                    if normalize_organization_key(submitted_value) == normalize_organization_key(
                        value
                    ):
                        raise AgentReviewError(
                            "review_answer_invalid",
                            "规范名称与投稿名称相同，无需保存未来路径纠正规则",
                        )
                    saved_canonical_path_correction = True
                    canonical_path_correction_reason = (
                        f"人工确认将投稿系所名称“{submitted_value}”规范为“{value}”。"
                    )
                rules.append("user_merge_similar_sibling")
            else:
                rules.append("user_keep_separate_sibling")
        if automatic_creation:
            current_level = _level_create(level, inferred_type, value, group)
            levels.append(current_level)
            current_id = proposed_organization_id(inferred_type, value, parent_id)
            if level == "university":
                university_level = current_level
            parent_id = current_id
            parent_name = value
            final_target_id = current_id
            final_target_is_new = True
            rules.append(f"clear_new_{level}_{inferred_type}")
            continue

        if misplaced_school_level:
            suggested_target = (
                suggestion.get("target_organization_id")
                if isinstance(suggestion, dict)
                else None
            )
            options = []
            if isinstance(suggested_target, str):
                options.append(
                    {
                        "value": "use-suggested",
                        "label": "采用清单建议的现有机构",
                        "requires": [],
                    }
                )
            options.extend(
                _question_options(
                    ("map-sibling", "映射到学校下现有同级机构", ["organization_id"]),
                    (
                        "create-sibling",
                        "在学校下新建同级机构",
                        [],
                        [
                            "organization_type",
                            "canonical_name",
                            "official_url",
                            "approved_domains",
                        ],
                    ),
                    ("use-parent", "忽略该系所字段并归入当前学院", []),
                    (
                        "create-child",
                        "作为当前学院的下级机构",
                        ["organization_type"],
                        ["canonical_name", "official_url", "approved_domains"],
                    ),
                    ("reject-group", "不收录这一组", []),
                )
            )
            question = _group_question(
                pull_number,
                group,
                kind="school_level_in_department",
                subject=value,
                level=level,
                prompt=f"系所字段“{value}”看起来是学院级机构，请确认实际归属",
                reason=ambiguity_reason,
                rule_default="create-sibling",
                context_recommendation=("use-suggested" if suggested_target else None),
                recommendation_confidence=(
                    "high"
                    if isinstance(suggestion, dict) and suggestion.get("source") == "history"
                    else "medium" if suggested_target else None
                ),
                path_correction_choices=("use-suggested", "map-sibling", "create-sibling"),
                options=options,
                context={
                    "submitted": copy.deepcopy(submitted),
                    "suggested_path_correction": copy.deepcopy(suggestion),
                    "university_organization_id": (
                        organizations.university_id(parent_id) if parent_id else None
                    ),
                },
                answers=answers,
            )
            questions.append(question)
            choice = _answer_choice(question)
            if choice is None:
                return None, questions, rules, creations
            answer = question["answer"]
            if choice == "reject-group":
                return (
                    _rejected_group_decision(
                        group["id"], answer.get("reason") or "机构层级无法确定"
                    ),
                    questions,
                    rules,
                    creations,
                )
            if choice == "use-parent":
                levels.append(_level_skip(level))
                rules.append("user_use_parent")
                break
            if choice == "create-child":
                organization_type = answer.get("organization_type")
                if organization_type not in LEVEL_TYPES["department"]:
                    raise AgentReviewError(
                        "review_answer_invalid",
                        "学院下级机构类型必须是 department、center 或 laboratory",
                    )
                levels.append(
                    _level_create(
                        "department",
                        organization_type,
                        answer.get("canonical_name") or value,
                        group,
                    )
                )
                final_target_id = proposed_organization_id(
                    organization_type,
                    answer.get("canonical_name") or value,
                    parent_id,
                )
                final_target_is_new = True
                rules.append("user_create_child")
                break

            target_id: str | None = None
            target_type: str | None = None
            if choice == "use-suggested":
                if not isinstance(suggestion, dict):
                    raise AgentReviewError("review_answer_invalid", "当前问题没有可采用的建议")
                target_id = suggestion.get("target_organization_id")
                target = organizations.by_id.get(target_id)
                target_type = target.get("type") if target else None
                if target is None:
                    raise AgentReviewError("review_answer_invalid", "清单建议的机构不存在")
            elif choice == "map-sibling":
                target_id = answer.get("organization_id")
                target = organizations.by_id.get(target_id)
                target_type = target.get("type") if target else None
                if target is None or target_type not in LEVEL_TYPES["school"]:
                    raise AgentReviewError("review_answer_invalid", "选择的同级学院或研究院无效")
            elif choice == "create-sibling":
                target_type = answer.get("organization_type") or (
                    "institute" if value.endswith("研究院") else "school"
                )
                canonical_name = answer.get("canonical_name") or value
                university_id = None
                if university_level and university_level["action"] == "existing":
                    university_id = university_level["organization_id"]
                elif university_level and university_level["action"] == "create":
                    university_id = proposed_organization_id(
                        "university",
                        university_level["canonical_name"],
                        None,
                    )
                if university_id is None:
                    raise AgentReviewError("review_answer_invalid", "无法确定新同级机构的学校上级")
                target_id = proposed_organization_id(target_type, canonical_name, university_id)
                creations.append(
                    {
                        "organization_id": target_id,
                        "organization_type": target_type,
                        "canonical_name": canonical_name,
                        "parent_id": university_id,
                        "official_url": answer.get("official_url"),
                        "approved_domains": answer.get("approved_domains", []),
                    }
                )
            else:
                raise AgentReviewError("review_answer_invalid", "机构层级问题的选择无效")

            corrected_levels = [
                copy.deepcopy(university_level) if university_level else _level_skip("university"),
                _level_skip("school"),
                _level_skip("department"),
            ]
            return (
                _resolved_group_decision(
                    group["id"],
                    levels=corrected_levels,
                    target_organization_id=target_id,
                    mapping_kind=_mapping_kind_for_target(value, target_type),
                    mapping_reason=answer.get("reason") or "人工确认系所字段实际为学校直属同级机构",
                    save_path_correction=bool(answer.get("save_path_correction")),
                ),
                questions,
                rules,
                creations,
            )

        question = _group_question(
            pull_number,
            group,
            kind="ambiguous_organization",
            subject=f"{level}:{value}",
            level=level,
            prompt=f"{level} 字段“{value}”无法自动确定，请选择处理方式",
            reason=ambiguity_reason,
            rule_default="create-submitted" if inferred_type else None,
            path_correction_choices=("map-existing",),
            options=_question_options(
                (
                    "create-submitted",
                    "按投稿名称新建当前层级机构",
                    ["organization_type"],
                    ["canonical_name", "official_url", "approved_domains"],
                ),
                ("map-existing", "直接映射到任意现有最终机构", ["organization_id"]),
                ("skip-level", "忽略当前字段并归入上级", []),
                ("reject-group", "不收录这一组", []),
            ),
            context={
                "submitted": copy.deepcopy(submitted),
                "parent_organization_id": parent_id,
                "candidate_ids": [item["id"] for item in candidates],
                "inferred_type": inferred_type,
            },
            answers=answers,
        )
        questions.append(question)
        choice = _answer_choice(question)
        if choice is None:
            return None, questions, rules, creations
        answer = question["answer"]
        if choice == "reject-group":
            return (
                _rejected_group_decision(
                    group["id"], answer.get("reason") or "机构归属无法确定"
                ),
                questions,
                rules,
                creations,
            )
        if choice == "skip-level":
            if level == "university" or any(
                normalize_text(submitted.get(item)) for item in LEVELS[index + 1 :]
            ):
                raise AgentReviewError(
                    "review_answer_invalid",
                    "学校不能跳过，跳过中间层级后也不能继续处理下级",
                )
            levels.append(_level_skip(level))
            rules.append(f"user_skip_{level}")
            continue
        if choice == "map-existing":
            target_id = answer.get("organization_id")
            target = organizations.by_id.get(target_id)
            if target is None:
                raise AgentReviewError(
                    "review_answer_invalid",
                    "所选现有机构不存在",
                )
            if (
                answer.get("save_path_correction")
                or organization_level(target["type"]) != level
                or target.get("parent_id") != parent_id
            ):
                return (
                    _resolved_group_decision(
                        group["id"],
                        levels=[],
                        target_organization_id=target_id,
                        mapping_kind="custom",
                        mapping_reason=answer.get("reason")
                        or "人工确认投稿路径应直接归入所选现有机构",
                        save_path_correction=bool(answer.get("save_path_correction")),
                    ),
                    questions,
                    rules,
                    creations,
                )
            current_level = _level_existing(level, target_id)
            levels.append(current_level)
            if level == "university":
                university_level = current_level
            parent_id = target_id
            parent_name = target["canonical_name"]
            final_target_id = target_id
            final_target_is_new = False
            rules.append(f"user_map_{level}")
            continue
        if choice != "create-submitted":
            raise AgentReviewError("review_answer_invalid", "机构问题的选择无效")
        organization_type = answer.get("organization_type") or inferred_type
        if organization_type not in LEVEL_TYPES[level]:
            raise AgentReviewError("review_answer_invalid", "新机构类型与当前层级不一致")
        canonical_name = answer.get("canonical_name") or value
        current_level = _level_create(level, organization_type, canonical_name, group)
        if answer.get("official_url") is not None:
            current_level["official_url"] = answer["official_url"]
        if answer.get("approved_domains"):
            current_level["approved_domains"] = sorted(set(answer["approved_domains"]))
        levels.append(current_level)
        if level == "university":
            university_level = current_level
        parent_id = proposed_organization_id(organization_type, canonical_name, parent_id)
        parent_name = canonical_name
        final_target_id = parent_id
        final_target_is_new = True
        rules.append(f"user_create_{level}")

    while len(levels) < len(LEVELS):
        levels.append(_level_skip(LEVELS[len(levels)]))

    decision = _resolved_group_decision(
        group["id"],
        levels=levels,
        target_organization_id=(
            final_target_id if saved_canonical_path_correction else None
        ),
        mapping_kind="custom" if saved_canonical_path_correction else "standard",
        mapping_reason=canonical_path_correction_reason,
        save_path_correction=saved_canonical_path_correction,
    )
    domain_target_id = final_target_id if final_target_id and not final_target_is_new else None
    if domain_target_id is None:
        domain_target_id = next(
            (
                item.get("organization_id")
                for item in reversed(decision["levels"])
                if item.get("action") == "existing" and item.get("organization_id")
            ),
            None,
        )
    if domain_target_id and not _organization_matches_sources(
        group,
        organizations,
        domain_target_id,
    ):
        question = _group_question(
            pull_number,
            group,
            kind="source_domain_mismatch",
            subject=domain_target_id,
            level=None,
            prompt="投稿来源域名不属于现有机构当前批准域名",
            reason="现有可信后端会拒绝来源域名不兼容的机构路径。",
            rule_default=None,
            options=_question_options(
                ("approve-domains", "把本次来源域名加入该现有机构", []),
                ("map-existing", "改为其他现有机构", ["organization_id"]),
                ("reject-group", "不收录这一组", []),
            ),
            context={
                "target_organization_id": domain_target_id,
                "source_domains": group.get("source_domains", []),
                "approved_domains": organizations.domains(domain_target_id),
            },
            answers=answers,
        )
        questions.append(question)
        choice = _answer_choice(question)
        if choice is None:
            return None, questions, rules, creations
        answer = question["answer"]
        if choice == "reject-group":
            return (
                _rejected_group_decision(
                    group["id"], answer.get("reason") or "来源域名与机构不一致"
                ),
                questions,
                rules,
                creations,
            )
        if choice == "map-existing":
            target_id = answer.get("organization_id")
            target = organizations.by_id.get(target_id)
            if target is None or not _organization_matches_sources(group, organizations, target_id):
                raise AgentReviewError(
                    "review_answer_invalid",
                    "替代机构不存在或仍不接受本次来源域名",
                )
            decision = _resolved_group_decision(
                group["id"],
                levels=[],
                target_organization_id=target_id,
                mapping_kind="custom",
                mapping_reason=answer.get("reason") or "人工确认来源域名对应的最终机构",
            )
        elif choice == "approve-domains":
            target = organizations.by_id[domain_target_id]
            approved_domains = sorted(set(group.get("source_domains", [])))
            if target["type"] == "university":
                approved_domains = sorted(
                    {university_domain_from_source(item) for item in approved_domains}
                )
            replaced = False
            for item in reversed(decision["levels"]):
                if item.get("organization_id") == domain_target_id:
                    item.update(
                        {
                            "action": "create",
                            "organization_id": None,
                            "organization_type": target["type"],
                            "canonical_name": target["canonical_name"],
                            "official_url": None,
                            "approved_domains": approved_domains,
                            "save_submitted_as_alias": False,
                        }
                    )
                    replaced = True
                    break
            if not replaced:
                raise AgentReviewError(
                    "review_answer_invalid",
                    "当前路径无法安全追加来源域名，请改为其他机构或拒绝",
                )
            rules.append("user_approve_source_domains")
        else:
            raise AgentReviewError("review_answer_invalid", "来源域名问题的选择无效")

    return decision, questions, rules, creations


def _row_question(
    pull_number: int,
    group: dict[str, Any],
    row: dict[str, Any],
    *,
    kind: str,
    prompt: str,
    reason: str,
    rule_default: str | None,
    options: list[dict[str, Any]],
    context: dict[str, Any],
    answers: dict[str, Any],
) -> dict[str, Any]:
    return _group_question(
        pull_number,
        group,
        kind=kind,
        subject=row["proposal_id"],
        level=None,
        prompt=prompt,
        reason=reason,
        rule_default=rule_default,
        options=options,
        context={
            "proposal_id": row["proposal_id"],
            "batch_row": row["batch_row"],
            "name": row["name"],
            "email": row["email"],
            **context,
        },
        answers=answers,
    )


def _apply_row_questions(
    pull_number: int,
    group: dict[str, Any],
    decision: dict[str, Any],
    answers: dict[str, Any],
    organizations: ManifestOrganizations,
    questions: list[dict[str, Any]],
) -> None:
    if decision["action"] == "reject":
        return
    target_id = decision.get("target_organization_id")
    if target_id is None:
        for level in reversed(decision["levels"]):
            if level["action"] == "existing":
                target_id = level["organization_id"]
                break
            if level["action"] == "create":
                parent_id: str | None = None
                for preceding in decision["levels"]:
                    if preceding is level:
                        break
                    if preceding["action"] == "existing":
                        parent_id = preceding["organization_id"]
                    elif preceding["action"] == "create":
                        parent_id = proposed_organization_id(
                            preceding["organization_type"],
                            preceding["canonical_name"],
                            parent_id,
                        )
                target_id = proposed_organization_id(
                    level["organization_type"], level["canonical_name"], parent_id
                )
                break

    for row in group["rows"]:
        conflict = row.get("record_conflict")
        if isinstance(conflict, dict):
            question = _row_question(
                pull_number,
                group,
                row,
                kind="record_conflict",
                prompt=f"{row['name']} 与已有导师资料冲突，请决定本次投稿如何处理",
                reason="普通机构审核不能覆盖已有导师资料。",
                rule_default="reject-row",
                options=_question_options(
                    ("reject-row", "不收录本行", []),
                    ("reject-group", "不收录整个机构分组", []),
                ),
                context={"record_conflict": copy.deepcopy(conflict)},
                answers=answers,
            )
            questions.append(question)
            choice = _answer_choice(question)
            if choice == "reject-group":
                replacement = _rejected_group_decision(
                    group["id"],
                    question["answer"].get("reason") or "分组包含无法采纳的资料冲突",
                )
                decision.clear()
                decision.update(replacement)
                return
            if choice == "reject-row":
                decision["row_overrides"].append(
                    {
                        "proposal_id": row["proposal_id"],
                        "action": "reject",
                        "organization_id": None,
                        "reason": question["answer"].get("reason")
                        or "与已有记录的资料不一致，本次不采纳",
                    }
                )

        identity = row.get("identity")
        if not isinstance(identity, dict):
            continue
        current_ids = {
            item.get("organization_id")
            for item in identity.get("mentor", {}).get("affiliations", [])
            if item.get("status") == "current"
        }
        if target_id in current_ids:
            continue
        question = _row_question(
            pull_number,
            group,
            row,
            kind="identity_conflict",
            prompt=f"{row['name']} 已在其他机构任职，请判断双聘、调动或拒绝",
            reason="任职关系变化不能由机构名称规则推断。",
            rule_default="reject-row",
            options=_question_options(
                ("reject-row", "不收录本行", []),
                ("dual", "新增当前双聘任职", ["reason"]),
                ("transfer", "调动到新机构", ["former_affiliation_id", "reason"]),
            ),
            context={"identity": copy.deepcopy(identity)},
            answers=answers,
        )
        questions.append(question)
        choice = _answer_choice(question)
        if choice == "reject-row":
            decision["row_overrides"].append(
                {
                    "proposal_id": row["proposal_id"],
                    "action": "reject",
                    "organization_id": None,
                    "reason": question["answer"].get("reason")
                    or "任职关系无法确认，本次不采纳",
                }
            )
        elif choice == "dual":
            decision["identity_resolutions"].append(
                {
                    "proposal_id": row["proposal_id"],
                    "action": "append_current_affiliation",
                    "make_primary": bool(question["answer"].get("make_primary")),
                    "former_affiliation_id": None,
                    "reason": question["answer"]["reason"],
                }
            )
        elif choice == "transfer":
            decision["identity_resolutions"].append(
                {
                    "proposal_id": row["proposal_id"],
                    "action": "transfer_current_affiliation",
                    "make_primary": True,
                    "former_affiliation_id": question["answer"]["former_affiliation_id"],
                    "reason": question["answer"]["reason"],
                }
            )


def _organization_change_preview(
    group: dict[str, Any],
    rules: list[str],
    decision: dict[str, Any] | None,
    creations: list[dict[str, Any]],
    organizations: ManifestOrganizations,
) -> list[dict[str, Any]]:
    previews: dict[str, dict[str, Any]] = {}
    parent_id: str | None = None
    lineage_names: list[str] = []
    submitted = group["submitted"]
    levels = decision.get("levels", []) if decision is not None else []
    if not levels:
        for level in LEVELS:
            value = normalize_text(submitted.get(level))
            exact_rule = f"exact_{level}_match"
            clear_prefix = f"clear_new_{level}_"
            clear_rule = next((item for item in rules if item.startswith(clear_prefix)), None)
            if exact_rule in rules:
                existing = organizations.exact(level, parent_id, value)
                if existing is None:
                    break
                parent_id = existing["id"]
                lineage_names = list(existing.get("lineage_names", [value]))
            elif clear_rule:
                organization_type = clear_rule.removeprefix(clear_prefix)
                level_preview = _level_create(level, organization_type, value, group)
                organization_id = proposed_organization_id(
                    organization_type,
                    value,
                    parent_id,
                )
                lineage_names.append(value)
                previews[organization_id] = {
                    "action": "create",
                    "id": organization_id,
                    "type": organization_type,
                    "path": " / ".join(lineage_names),
                    "source": "rule",
                    "source_domains": list(group.get("source_domains", [])),
                    "official_urls": (
                        [level_preview["official_url"]]
                        if level_preview.get("official_url")
                        else []
                    ),
                    "approved_domains": list(level_preview.get("approved_domains", [])),
                }
                parent_id = organization_id
            elif not value or f"empty_{level}" in rules:
                continue
            else:
                break
    else:
        for level in levels:
            if level["action"] == "existing":
                existing = organizations.by_id.get(level["organization_id"])
                if existing is None:
                    break
                parent_id = existing["id"]
                lineage_names = list(
                    existing.get("lineage_names", [existing["canonical_name"]])
                )
            elif level["action"] == "create":
                organization_id = proposed_organization_id(
                    level["organization_type"],
                    level["canonical_name"],
                    parent_id,
                )
                lineage_names.append(level["canonical_name"])
                previews[organization_id] = {
                    "action": "create",
                    "id": organization_id,
                    "type": level["organization_type"],
                    "path": " / ".join(lineage_names),
                    "source": (
                        "rule"
                        if any(item.startswith(f"clear_new_{level['level']}_") for item in rules)
                        else "user-decision"
                    ),
                    "source_domains": list(group.get("source_domains", [])),
                    "official_urls": (
                        [level["official_url"]] if level.get("official_url") else []
                    ),
                    "approved_domains": list(level.get("approved_domains", [])),
                }
                parent_id = organization_id

    known_paths = {
        item["id"]: " / ".join(item.get("lineage_names", []))
        for item in organizations.organizations
    }
    known_paths.update({item["id"]: item["path"] for item in previews.values()})
    for creation in creations:
        parent_path = known_paths.get(creation.get("parent_id"), "")
        path = " / ".join(
            item for item in (parent_path, creation["canonical_name"]) if item
        )
        previews[creation["organization_id"]] = {
            "action": "create",
            "id": creation["organization_id"],
            "type": creation["organization_type"],
            "path": path,
            "source": "user-decision",
            "source_domains": list(group.get("source_domains", [])),
            "official_urls": (
                [creation["official_url"]] if creation.get("official_url") else []
            ),
            "approved_domains": list(creation.get("approved_domains", [])),
        }
    return list(previews.values())


def _organization_creation_conflicts(
    previews: list[dict[str, Any]],
    organizations: ManifestOrganizations,
) -> list[dict[str, Any]]:
    """Expose same-name placements that need review before preflight."""

    conflicts: list[dict[str, Any]] = []
    for preview in previews:
        if preview.get("action") != "create":
            continue
        proposed_names = [item.strip() for item in preview["path"].split("/")]
        if not proposed_names:
            continue
        university_key = normalize_organization_key(proposed_names[0])
        name_key = normalize_organization_key(proposed_names[-1])
        if not university_key or not name_key:
            continue
        matches: list[dict[str, Any]] = []
        for organization in organizations.organizations:
            if organization_level(organization["type"]) != organization_level(
                preview["type"]
            ):
                continue
            lineage_names = organization.get("lineage_names", [])
            if not lineage_names or normalize_organization_key(lineage_names[0]) != university_key:
                continue
            organization_names = [
                organization["canonical_name"],
                *organization.get("aliases", []),
            ]
            if name_key not in {normalize_organization_key(item) for item in organization_names}:
                continue
            existing_path = " / ".join(lineage_names)
            if existing_path == preview["path"]:
                continue
            matches.append(
                {
                    "id": organization["id"],
                    "type": organization["type"],
                    "path": existing_path,
                    "parent_id": organization.get("parent_id"),
                }
            )
        for other in previews:
            if other["id"] == preview["id"] or other.get("action") != "create":
                continue
            if organization_level(other["type"]) != organization_level(preview["type"]):
                continue
            other_names = [item.strip() for item in other["path"].split("/")]
            if (
                other_names
                and normalize_organization_key(other_names[0]) == university_key
                and normalize_organization_key(other_names[-1]) == name_key
                and other["path"] != preview["path"]
            ):
                matches.append(
                    {
                        "id": other["id"],
                        "type": other["type"],
                        "path": other["path"],
                        "parent_id": None,
                        "planned": True,
                    }
                )
        if matches:
            conflicts.append(
                {
                    "kind": "same-name-different-parent",
                    "requires_human_decision": True,
                    "proposed": {
                        "id": preview["id"],
                        "type": preview["type"],
                        "path": preview["path"],
                    },
                    "matches": sorted(matches, key=lambda item: (item["path"], item["id"])),
                }
            )
    return sorted(
        conflicts,
        key=lambda item: (item["proposed"]["path"], item["proposed"]["id"]),
    )


def _resolved_path(
    decision: dict[str, Any],
    organizations: ManifestOrganizations,
    previews: list[dict[str, Any]],
) -> str | None:
    if decision.get("action") != "resolve":
        return None
    target_id = decision.get("target_organization_id")
    if isinstance(target_id, str):
        target = organizations.by_id.get(target_id)
        if target is not None:
            return " / ".join(target.get("lineage_names", [target["canonical_name"]]))
        target_preview = next(
            (item for item in previews if item.get("id") == target_id),
            None,
        )
        if target_preview is not None:
            return target_preview["path"]

    lineage_names: list[str] = []
    for level in decision.get("levels", []):
        if level.get("action") == "existing":
            existing = organizations.by_id.get(level.get("organization_id"))
            if existing is not None:
                lineage_names = list(
                    existing.get("lineage_names", [existing["canonical_name"]])
                )
        elif level.get("action") == "create":
            lineage_names.append(level["canonical_name"])
    return " / ".join(lineage_names) if lineage_names else None


def _path_normalization(
    group: dict[str, Any],
    group_plan: dict[str, Any],
    decision: dict[str, Any] | None,
    organizations: ManifestOrganizations,
    previews: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if decision is None or decision.get("action") != "resolve":
        return None
    submitted_path = group_path(group)
    resolved_path = _resolved_path(decision, organizations, previews)
    if resolved_path is None:
        return None
    rules = group_plan.get("auto_rules", [])
    normalization_rules = [
        rule
        for rule in rules
        if rule.startswith(("empty_", "user_skip_", "user_map_"))
        or rule
        in {
            "historical_path_correction",
            "repeated_parent_name",
            "repeated_university_name",
            "user_use_parent",
            "user_use_parent_for_similar_sibling",
            "user_merge_similar_sibling",
        }
    ]
    if submitted_path == resolved_path and not normalization_rules:
        return None
    if submitted_path != resolved_path and not normalization_rules:
        normalization_rules = ["canonical_path"]
    used_user_decision = any(rule.startswith("user_") for rule in normalization_rules)
    if "historical_path_correction" in normalization_rules:
        source = "saved-rule"
    else:
        source = "user-decision" if used_user_decision else "rule"
    save_path_correction = decision.get("save_path_correction") is True
    return {
        "group_id": group["id"],
        "submitted_path": submitted_path,
        "resolved_path": resolved_path,
        "row_count": len(group.get("rows", [])),
        "source": source,
        "rules": normalization_rules,
        "path_correction_scope": (
            "future-identical-path"
            if save_path_correction
            else "current-batch"
            if used_user_decision and submitted_path != resolved_path
            else None
        ),
    }


def plan_review(
    *,
    repository: str,
    pull: PullSnapshot,
    manifest: dict[str, Any],
    manifest_sha256: str,
    previous_answers: dict[str, Any] | None = None,
    latest_organizations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    organization_values = {
        item["id"]: copy.deepcopy(item) for item in manifest["organizations"]
    }
    for item in latest_organizations or []:
        organization_values[item["id"]] = copy.deepcopy(item)
    organizations = ManifestOrganizations(list(organization_values.values()))
    answers = copy.deepcopy(previous_answers or {})
    group_plans: list[dict[str, Any]] = []
    all_questions: list[dict[str, Any]] = []
    all_creations: list[dict[str, Any]] = []
    organization_change_preview: dict[str, dict[str, Any]] = {}
    path_normalizations: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    similar_new_departments = _similar_new_department_contexts(manifest["groups"])
    planned_cross_parents = _planned_cross_parent_contexts(
        manifest["groups"],
        organizations,
    )
    for group in sorted(manifest["groups"], key=lambda item: item["id"]):
        decision, questions, rules, creations = _plan_path(
            pull.number,
            group,
            organizations,
            answers,
            similar_new_departments.get(group["id"]),
            planned_cross_parents.get(group["id"]),
        )
        if decision is not None:
            _apply_row_questions(
                pull.number,
                group,
                decision,
                answers,
                organizations,
                questions,
            )
        previews = _organization_change_preview(
            group,
            rules,
            decision,
            creations,
            organizations,
        )
        for preview in previews:
            organization_change_preview[preview["id"]] = preview
        if decision is not None:
            declared_conflict_pairs = {
                tuple(sorted(pair))
                for question in questions
                for pair in question.get("context", {}).get("conflict_pair_ids", [])
                if len(pair) == 2
            }
            current_preview_ids = {item["id"] for item in previews}
            preview_conflicts = [
                item
                for item in _organization_creation_conflicts(
                    list(organization_change_preview.values()),
                    organizations,
                )
                if item["proposed"]["id"] in current_preview_ids
            ]
            for conflict in preview_conflicts:
                proposed = conflict["proposed"]
                uncovered_matches = [
                    item
                    for item in conflict["matches"]
                    if tuple(sorted((proposed["id"], item["id"])))
                    not in declared_conflict_pairs
                ]
                if not uncovered_matches:
                    continue
                question = _group_question(
                    pull.number,
                    group,
                    kind="same_name_different_parent",
                    subject="preview|"
                    + proposed["id"]
                    + "|"
                    + "|".join(item["id"] for item in uncovered_matches),
                    level=organization_level(proposed["type"]),
                    prompt=(
                        "规范化后将在另一父级下创建同名机构"
                        f"“{proposed['path'].rsplit(' / ', 1)[-1]}”，请确认"
                    ),
                    reason="修改后的规范名称与同校其他父级下的机构重名，需要明确确认当前归属。",
                    rule_default="keep-placement",
                    options=_question_options(
                        ("keep-placement", "确认保留当前父级归属", []),
                    ),
                    context={
                        "submitted": copy.deepcopy(group["submitted"]),
                        "proposed": copy.deepcopy(proposed),
                        "matches": copy.deepcopy(uncovered_matches),
                        "conflict_pair_ids": [
                            sorted((proposed["id"], item["id"]))
                            for item in uncovered_matches
                        ],
                    },
                    answers=answers,
                )
                questions.append(question)
        pending = [item for item in questions if item["status"] == "pending"]
        state = "pending" if pending else ("answered" if questions else "auto")
        group_plan = {
            "id": group["id"],
            "path": group_path(group),
            "row_count": len(group["rows"]),
            "state": state,
            "auto_rules": rules,
            "question_ids": [item["id"] for item in questions],
            "decision": (
                copy.deepcopy(decision) if decision is not None and not pending else None
            ),
        }
        group_plans.append(group_plan)
        normalization = _path_normalization(
            group,
            group_plan,
            decision if not pending else None,
            organizations,
            previews,
        )
        if normalization is not None:
            path_normalizations.append(normalization)
        all_questions.extend(questions)
        all_creations.extend(creations)
        if decision is not None and not pending:
            decisions.append(decision)

    current_question_ids = {item["id"] for item in all_questions}
    answers = {key: value for key, value in answers.items() if key in current_question_ids}
    pending_count = sum(item["status"] == "pending" for item in all_questions)
    complete = pending_count == 0 and len(decisions) == len(manifest["groups"])
    organization_creations = {
        item["organization_id"]: item for item in all_creations
    }
    preview_values = [
        organization_change_preview[key] for key in sorted(organization_change_preview)
    ]
    if pending_count == 0:
        preview_ids = {item["id"] for item in preview_values}
        missing_planned_targets = sorted(
            {
                item.get("answer", {}).get("organization_id")
                for item in all_questions
                if item.get("answer", {}).get("choice") == "map-planned"
                and item.get("answer", {}).get("organization_id") not in preview_ids
            }
        )
        if missing_planned_targets:
            raise AgentReviewError(
                "review_answer_invalid",
                "映射到的本批次计划机构未被其他分组保留，请修改跨父级同名机构的决定",
            )
    organization_conflicts = _organization_creation_conflicts(
        preview_values,
        organizations,
    )
    acknowledged_conflict_pairs: set[tuple[str, str]] = set()
    for question in all_questions:
        if (
            question.get("type") == "same_name_different_parent"
            and question.get("status") == "answered"
            and question.get("answer", {}).get("choice") == "keep-placement"
        ):
            acknowledged_conflict_pairs.update(
                tuple(sorted(item))
                for item in question.get("context", {}).get("conflict_pair_ids", [])
                if len(item) == 2
            )
    for conflict in organization_conflicts:
        acknowledged = all(
            tuple(sorted((conflict["proposed"]["id"], item["id"])))
            in acknowledged_conflict_pairs
            for item in conflict["matches"]
        )
        conflict["acknowledged"] = acknowledged
        conflict["requires_human_decision"] = not acknowledged
    decision_document = None
    if complete:
        decision_document = {
            "schema_version": 1,
            "kind": "batch_organization_review_decision",
            "pull_request_number": pull.number,
            "issue_number": pull.issue_number,
            "manifest_sha256": manifest_sha256,
            "organization_creations": [
                organization_creations[key] for key in sorted(organization_creations)
            ],
            "decisions": sorted(decisions, key=lambda item: item["group_id"]),
        }

    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "kind": "agent_organization_review_draft",
        "repository": repository,
        "pull": pull.as_dict(),
        "manifest_sha256": manifest_sha256,
        "registry_sha256": manifest["registry_sha256"],
        "planned_at": utc_now(),
        "answers": answers,
        "groups": group_plans,
        "questions": all_questions,
        "organization_change_preview": preview_values,
        "organization_conflicts": organization_conflicts,
        "path_normalizations": sorted(
            path_normalizations,
            key=lambda item: (item["submitted_path"], item["group_id"]),
        ),
        "decision": decision_document,
        "preflight": None,
        "submission": None,
        "summary": {
            "groups": len(group_plans),
            "rows": sum(item["row_count"] for item in group_plans),
            "invalid_rows": len(manifest.get("invalid_rows", [])),
            "auto_groups": sum(item["state"] == "auto" for item in group_plans),
            "answered_groups": sum(item["state"] == "answered" for item in group_plans),
            "pending_groups": sum(item["state"] == "pending" for item in group_plans),
            "questions": len(all_questions),
            "pending_questions": pending_count,
            "complete": complete,
        },
    }


def validate_answer(question: dict[str, Any], answer: dict[str, Any]) -> None:
    choice = answer.get("choice")
    option = next(
        (item for item in question.get("options", []) if item.get("value") == choice),
        None,
    )
    if option is None:
        allowed = ", ".join(item["value"] for item in question.get("options", []))
        raise AgentReviewError(
            "review_answer_invalid",
            f"问题 {question['id']} 不支持选择 {choice!r}；可选：{allowed}",
        )
    missing = [field for field in option.get("requires", []) if not answer.get(field)]
    if missing:
        raise AgentReviewError(
            "review_answer_incomplete",
            f"选择 {choice} 还需要参数：{', '.join(missing)}",
        )
    if answer.get("save_path_correction") and choice not in question.get(
        "path_correction_choices", []
    ):
        raise AgentReviewError(
            "review_answer_invalid",
            f"问题 {question['id']} 不支持保存未来路径纠正规则",
        )


def compact_decision_for_comment(decision: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: copy.deepcopy(value)
        for key, value in decision.items()
        if key not in {"organization_creations", "decisions"}
    }
    compact["encoding"] = COMPACT_REVIEW_ENCODING
    if decision.get("organization_creations"):
        compact["organization_creations"] = copy.deepcopy(
            decision["organization_creations"]
        )
    shared_levels: list[dict[str, Any]] = []
    shared_index: dict[str, int] = {}
    compact_decisions: list[dict[str, Any]] = []
    for group_decision in decision["decisions"]:
        item = {
            key: copy.deepcopy(value)
            for key, value in group_decision.items()
            if key != "levels"
        }
        refs: list[int] = []
        for level in group_decision.get("levels", []):
            key = json.dumps(level, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            index = shared_index.get(key)
            if index is None:
                index = len(shared_levels)
                shared_index[key] = index
                shared_levels.append(copy.deepcopy(level))
            refs.append(index)
        item["level_refs"] = refs
        compact_decisions.append(item)
    compact["level_decisions"] = shared_levels
    compact["decisions"] = compact_decisions
    return compact


def decision_comment_body(decision: dict[str, Any]) -> str:
    values = [decision, compact_decision_for_comment(decision)]
    bodies = [
        f"{REVIEW_COMMENT_MARKER}\n```json\n"
        f"{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n```"
        for value in values
    ]
    body = min(bodies, key=len)
    if len(body) > COMMENT_CHARACTER_LIMIT:
        raise AgentReviewError(
            "review_comment_too_large",
            f"审核评论有 {len(body)} 个字符，超过 {COMMENT_CHARACTER_LIMIT} 字符上限",
        )
    return body


def manifest_summary(
    pull: PullSnapshot,
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    rows = [row for group in manifest["groups"] for row in group["rows"]]
    return {
        "pr": pull.number,
        "issue": pull.issue_number,
        "title": pull.title,
        "draft": pull.draft,
        "status_label": pull.status_label,
        "head_sha": pull.head_sha,
        "manifest_sha256": manifest_sha256,
        "groups": len(manifest["groups"]),
        "rows": len(rows),
        "invalid_rows": len(manifest.get("invalid_rows", [])),
        "identity_conflicts": sum(isinstance(row.get("identity"), dict) for row in rows),
        "record_conflicts": sum(
            isinstance(row.get("record_conflict"), dict) for row in rows
        ),
        "path_suggestions": sum(
            isinstance(group.get("suggested_path_correction"), dict)
            for group in manifest["groups"]
        ),
    }


def project_fields(value: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    if not fields:
        return value
    unknown = [field for field in fields if field not in value]
    if unknown:
        raise AgentReviewError(
            "review_fields_invalid",
            f"未知输出字段：{', '.join(unknown)}；可选：{', '.join(sorted(value))}",
        )
    return {field: value[field] for field in fields}
