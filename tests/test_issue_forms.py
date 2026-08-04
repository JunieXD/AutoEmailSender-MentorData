from __future__ import annotations

from pathlib import Path

import yaml

from mentor_data.batch import BATCH_FORM_LABELS
from mentor_data.github_events import parse_issue_form
from mentor_data.proposals import SINGLE_FORM_LABELS
from mentor_data.reporting import REPORT_FORM_LABELS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}
CC_BY_4_URL = "https://creativecommons.org/licenses/by/4.0/"


def _load_form(name: str) -> dict:
    path = PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _field_labels(document: dict) -> set[str]:
    return {
        component["attributes"]["label"]
        for component in document["body"]
        if component.get("type") != "markdown"
    }


def _field_ids(document: dict) -> set[str]:
    return {
        component["id"]
        for component in document["body"]
        if component.get("type") != "markdown"
    }


def _markdown_text(document: dict) -> str:
    return "\n".join(
        component["attributes"]["value"]
        for component in document["body"]
        if component.get("type") == "markdown"
    )


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


def test_current_form_labels_are_recognized_by_automation() -> None:
    forms = [
        ("batch-contribution.yml", BATCH_FORM_LABELS),
        ("contribute-mentor.yml", SINGLE_FORM_LABELS),
        ("report-error.yml", REPORT_FORM_LABELS),
    ]
    for filename, aliases in forms:
        current_labels = _field_labels(_load_form(filename))
        for canonical, alternate_labels in aliases.items():
            accepted_labels = {canonical, *alternate_labels}
            assert len(current_labels & accepted_labels) == 1, (filename, canonical)


def test_text_fields_explain_what_to_enter_and_show_an_example() -> None:
    for filename in ("batch-contribution.yml", "contribute-mentor.yml", "report-error.yml"):
        document = _load_form(filename)
        for component in document["body"]:
            if component.get("type") not in {"input", "textarea"}:
                continue
            attributes = component["attributes"]
            assert attributes.get("description"), (filename, component["id"], "description")
            assert attributes.get("placeholder"), (filename, component["id"], "placeholder")


def test_old_issue_headings_remain_compatible_after_copy_changes() -> None:
    body = "\n\n".join(
        [
            "### 社区共享包\n\n[community-share.xlsx](https://example.test/file.xlsx)",
            "### 补充说明\n\n_No response_",
            "### 投稿确认\n\n- [x] 我确认",
        ]
    )
    sections = parse_issue_form(body, BATCH_FORM_LABELS)
    assert sections["社区共享包"].startswith("[community-share.xlsx]")
    assert sections["补充说明"] == ""


def test_batch_form_gives_first_time_contributors_a_short_path() -> None:
    document = _load_form("batch-contribution.yml")
    guidance = _markdown_text(document)
    assert document["name"] == "上传导师表格（推荐）"
    assert all(step in guidance for step in ("1.", "2.", "3."))
    assert "贡献到社区" in guidance
    assert "多个学校和学院" in guidance
    assert "建议每次只上传同一所学校、同一个学院" in guidance
    assert "尽量使用官网全称" in guidance
    assert "用了简称也没关系" in guidance
    assert "不需要打开或修改" in guidance
    assert "[批量投稿] XXX大学XXX学院" in guidance


def test_manual_form_uses_plain_labels_and_marks_optional_fields() -> None:
    document = _load_form("contribute-mentor.yml")
    labels = _field_labels(document)
    guidance = _markdown_text(document)
    title_field = next(
        component
        for component in document["body"]
        if component.get("id") == "academic_title"
    )
    assert {"必填：这位老师是谁", "选填：更多公开信息", "必填：信息来自哪里"} <= {
        line.removeprefix("### ") for line in guidance.splitlines()
    }
    assert "社区机构 ID" not in labels
    assert "官方证据页面" not in labels
    assert {
        "更具体的单位（选填）",
        "职称（选填）",
        "研究方向（选填）",
        "代表论文（选填）",
        "老师的高校官网详情页（选填）",
        "发现这位老师的来源页面",
    } <= labels
    assert title_field["type"] == "input"
    assert "官网没有写时留空" in title_field["attributes"]["description"]
    assert "[导师投稿] XXX大学XXX老师" in guidance
    assert "现有信息直接填入" in guidance
    assert "不需要复制粘贴" in guidance


def test_manual_form_ids_match_software_prefill_parameters() -> None:
    field_ids = _field_ids(_load_form("contribute-mentor.yml"))

    assert {
        "name",
        "email",
        "university",
        "school",
        "department",
        "academic_title",
        "research_direction",
        "recent_papers",
        "profile_url",
        "source_url",
        "consent",
    } == field_ids
    assert "title" not in field_ids


def test_removed_single_form_organization_heading_is_ignored_in_old_issues() -> None:
    current_body = "\n\n".join(
        f"### {label}\n\n{'我确认' if label == '投稿确认' else '_No response_'}"
        for label in SINGLE_FORM_LABELS
    )
    body = "### 社区机构 ID\n\norg_old_internal\n\n" + current_body

    sections = parse_issue_form(body, SINGLE_FORM_LABELS)

    assert "社区机构 ID" not in sections


def test_report_form_asks_people_about_the_problem_in_plain_language() -> None:
    document = _load_form("report-error.yml")
    labels = _field_labels(document)
    guidance = _markdown_text(document)
    assert {
        "要反馈的导师（软件自动填写）",
        "发现了什么问题",
        "哪里有问题",
        "正确内容应该是什么",
        "可以证明的高校官网页面",
    } <= labels
    assert not {"社区导师 ID", "涉及字段", "当前社区值", "建议值或处理方式"} & labels
    assert "[信息反馈] XXX大学XXX老师" in guidance
    assert "标题、导师信息和“现在显示的内容”会自动填写" in guidance
