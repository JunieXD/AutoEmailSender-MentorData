from __future__ import annotations

import hashlib
from typing import Any


def stable_proposal_entity_id(prefix: str, proposal: dict[str, Any], suffix: str) -> str:
    seed = (
        f"{proposal['id']}:{proposal['issue']['url']}:"
        f"{proposal['contributor']['github_user_id']}:{suffix}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def proposed_mentor_id(proposal: dict[str, Any]) -> str:
    return stable_proposal_entity_id("mentor", proposal, "mentor")
