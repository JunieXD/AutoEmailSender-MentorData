from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}


def test_issue_forms_use_only_supported_components_and_unique_ids() -> None:
    template_root = PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE"
    paths = sorted(path for path in template_root.glob("*.yml") if path.name != "config.yml")
    assert paths
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document.get("name"), str), path
        assert isinstance(document.get("description"), str), path
        assert isinstance(document.get("body"), list), path
        ids: list[str] = []
        for component in document["body"]:
            component_type = component.get("type")
            assert component_type in SUPPORTED_FORM_TYPES, (path, component_type)
            if component_type != "markdown":
                assert isinstance(component.get("id"), str), (path, component)
                ids.append(component["id"])
        assert len(ids) == len(set(ids)), path
