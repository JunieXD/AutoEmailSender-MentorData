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

    def ancestors(self, organization_id: str) -> list[dict[str, Any]]:
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
        return result

    def lineage(self, organization_id: str) -> list[dict[str, Any]]:
        return list(reversed(self.ancestors(organization_id)))

    def match(self, name: str, *, parent_id: str | None) -> OrganizationMatch:
        key = normalize_organization_key(name)
        if not key:
            return OrganizationMatch("unknown", None)
        candidates: list[str] = []
        for organization in self.organizations:
            if organization.get("parent_id") != parent_id:
                continue
            values = [organization["canonical_name"], *organization.get("aliases", [])]
            if key in {normalize_organization_key(item) for item in values}:
                candidates.append(organization["id"])
        if len(candidates) == 1:
            return OrganizationMatch("matched", candidates[0], tuple(candidates))
        if candidates:
            return OrganizationMatch("ambiguous", None, tuple(sorted(candidates)))
        return OrganizationMatch("unknown", None)

    def approved_domains(self, organization_id: str) -> set[str]:
        domains: set[str] = set()
        for organization in self.ancestors(organization_id):
            domains.update(item.lower() for item in organization.get("approved_domains", []))
        return domains

    def url_is_approved(self, value: str, organization_id: str) -> bool:
        hostname = hostname_for_url(value)
        return any(
            host_matches_domain(hostname, domain)
            for domain in self.approved_domains(organization_id)
        )

    def projection_names(self, organization_id: str) -> dict[str, str | None]:
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
        return {
            "university": university,
            "school": school,
            "department": department,
        }

    def shard_ids(self, organization_id: str) -> tuple[str, str]:
        lineage = self.lineage(organization_id)
        university = next((item for item in lineage if item["type"] == "university"), None)
        if university is None:
            raise ValueError(f"机构 {organization_id} 没有大学祖先")
        unit = next(
            (item for item in lineage if item.get("parent_id") == university["id"]),
            university,
        )
        return university["id"], unit["id"]
