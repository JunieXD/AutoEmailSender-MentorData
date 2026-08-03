from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}
CC_BY_4_URL = "https://creativecommons.org/licenses/by/4.0/"


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


def test_all_submission_forms_require_explicit_cc_by_4_consent() -> None:
    template_root = PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE"
    paths = [
        template_root / "contribute-mentor.yml",
        template_root / "batch-contribution.yml",
        template_root / "report-error.yml",
    ]
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        checkbox_labels = [
            option["label"]
            for component in document["body"]
            if component.get("type") == "checkboxes"
            for option in component.get("attributes", {}).get("options", [])
            if option.get("required") is True
        ]
        assert checkbox_labels, path
        assert any("CC BY 4.0" in label and CC_BY_4_URL in label for label in checkbox_labels), path
