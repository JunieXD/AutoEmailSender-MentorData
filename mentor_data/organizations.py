from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .normalization import (
    host_matches_domain,
    hostname_for_url,
    normalize_organization_key,
)


@dataclass(frozen=True, slots=True)
class OrganizationMatch:
    status: str
    organization_id: str | None
    candidate_ids: tuple[str, ...] = ()


class OrganizationRegistry:
    def __init__(self, organizations: list[dict[str, Any]]) -> None:
        self.organizations = organizations
        self.by_id = {item["id"]: item for item in organizations}
        self._name_index: dict[tuple[str | None, str], list[str]] = {}
        for organization in organizations:
            parent_id = organization.get("parent_id")
            values = [organization["canonical_name"], *organization.get("aliases", [])]
            for value in values:
                key = normalize_organization_key(value)
                if key:
                    self._name_index.setdefault((parent_id, key), []).append(organization["id"])
        self._ancestors_cache: dict[str, tuple[dict[str, Any], ...]] = {}
        self._lineage_cache: dict[str, tuple[dict[str, Any], ...]] = {}
        self._approved_domains_cache: dict[str, frozenset[str]] = {}
        self._projection_names_cache: dict[str, dict[str, str | None]] = {}
        self._shard_ids_cache: dict[str, tuple[str, str]] = {}

    def _ancestors(self, organization_id: str) -> tuple[dict[str, Any], ...]:
        cached = self._ancestors_cache.get(organization_id)
        if cached is not None:
            return cached
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        current_id: str | None = organization_id
        while current_id is not None:
            if current_id in seen or current_id not in self.by_id:
                break
            seen.add(current_id)
            organization = self.by_id[current_id]
            result.append(organization)
            current_id = organization.get("parent_id")
        resolved = tuple(result)
        self._ancestors_cache[organization_id] = resolved
        return resolved

    def ancestors(self, organization_id: str) -> list[dict[str, Any]]:
        return list(self._ancestors(organization_id))

    def lineage(self, organization_id: str) -> list[dict[str, Any]]:
        cached = self._lineage_cache.get(organization_id)
        if cached is None:
            cached = tuple(reversed(self._ancestors(organization_id)))
            self._lineage_cache[organization_id] = cached
        return list(cached)

    def match(self, name: str, *, parent_id: str | None) -> OrganizationMatch:
        key = normalize_organization_key(name)
        if not key:
            return OrganizationMatch("unknown", None)
        candidates = list(dict.fromkeys(self._name_index.get((parent_id, key), [])))
        if len(candidates) == 1:
            return OrganizationMatch("matched", candidates[0], tuple(candidates))
        if candidates:
            return OrganizationMatch("ambiguous", None, tuple(sorted(candidates)))
        return OrganizationMatch("unknown", None)

    def approved_domains(self, organization_id: str) -> set[str]:
        cached = self._approved_domains_cache.get(organization_id)
        if cached is None:
            domains: set[str] = set()
            for organization in self._ancestors(organization_id):
                domains.update(item.lower() for item in organization.get("approved_domains", []))
            cached = frozenset(domains)
            self._approved_domains_cache[organization_id] = cached
        return set(cached)

    def url_is_approved(self, value: str, organization_id: str) -> bool:
        hostname = hostname_for_url(value)
        return any(
            host_matches_domain(hostname, domain)
            for domain in self._approved_domains(organization_id)
        )

    def _approved_domains(self, organization_id: str) -> frozenset[str]:
        cached = self._approved_domains_cache.get(organization_id)
        if cached is None:
            self.approved_domains(organization_id)
            cached = self._approved_domains_cache[organization_id]
        return cached

    def projection_names(self, organization_id: str) -> dict[str, str | None]:
        cached = self._projection_names_cache.get(organization_id)
        if cached is not None:
            return dict(cached)
        university: str | None = None
        school: str | None = None
        department: str | None = None
        for organization in self.lineage(organization_id):
            organization_type = organization["type"]
            if organization_type == "university":
                university = organization["canonical_name"]
            elif organization_type in {"school", "institute"} and school is None:
                school = organization["canonical_name"]
            else:
                department = organization["canonical_name"]
        result = {
            "university": university,
            "school": school,
            "department": department,
        }
        self._projection_names_cache[organization_id] = result
        return dict(result)

    def shard_ids(self, organization_id: str) -> tuple[str, str]:
        cached = self._shard_ids_cache.get(organization_id)
        if cached is not None:
            return cached
        lineage = self.lineage(organization_id)
        university = next((item for item in lineage if item["type"] == "university"), None)
        if university is None:
            raise ValueError(f"机构 {organization_id} 没有大学祖先")
        unit = next(
            (item for item in lineage if item.get("parent_id") == university["id"]),
            university,
        )
        result = (university["id"], unit["id"])
        self._shard_ids_cache[organization_id] = result
        return result
