const REPOSITORY = "JunieXD/AutoEmailSender-MentorData";
const COMMENT_MARKER = "<!-- mentor-data-organization-review:v1 -->";
const BRANCH_PATTERN = /^batch\/issue-([1-9][0-9]*)-([1-9][0-9]*)$/;
const SHA_PATTERN = /^[a-f0-9]{40,64}$/;
const DOMAIN_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;
const MAX_MANIFEST_BYTES = 20_000_000;
const MAX_COMMENT_BYTES = 200_000;
const LEVELS = ["university", "school", "department"];
const LEVEL_LABELS = {
  university: "学校",
  school: "学院 / 研究院",
  department: "系所 / 中心 / 实验室",
};
const LEVEL_TYPES = {
  university: ["university"],
  school: ["school", "institute"],
  department: ["department", "center", "laboratory"],
};
const TYPE_LABELS = {
  university: "大学",
  school: "学院",
  institute: "研究院",
  department: "系 / 部门",
  center: "中心",
  laboratory: "实验室",
};

const state = {
  pullNumber: null,
  pullUrl: null,
  issueNumber: null,
  manifest: null,
  manifestSha256: null,
  organizationById: new Map(),
  organizationLabelById: new Map(),
  organizationIdByLabel: new Map(),
  cards: [],
};

const nodes = {
  status: document.querySelector("#review-status"),
  statusTitle: document.querySelector("#status-title"),
  statusDetail: document.querySelector("#status-detail"),
  summary: document.querySelector("#review-summary"),
  issue: document.querySelector("#review-issue"),
  groupCount: document.querySelector("#review-group-count"),
  rowCount: document.querySelector("#review-row-count"),
  invalidCount: document.querySelector("#review-invalid-count"),
  invalidPanel: document.querySelector("#invalid-rows-panel"),
  invalidRows: document.querySelector("#invalid-rows"),
  groupsSection: document.querySelector("#groups-section"),
  groups: document.querySelector("#review-groups"),
  emptyReview: document.querySelector("#empty-review"),
  decisionPanel: document.querySelector("#decision-panel"),
  generate: document.querySelector("#generate-decision"),
  decisionError: document.querySelector("#decision-error"),
  decisionOutput: document.querySelector("#decision-output"),
  decisionText: document.querySelector("#decision-text"),
  copyOpen: document.querySelector("#copy-open-pr"),
  copyStatus: document.querySelector("#copy-status"),
};

let floatingControlSequence = 0;
let activeFloatingControl = null;

function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function setStatus(kind, title, detail) {
  nodes.status.dataset.kind = kind;
  nodes.statusTitle.textContent = title;
  nodes.statusDetail.textContent = detail;
}

function normalizeOrganizationName(value) {
  return String(value || "")
    .normalize("NFKC")
    .trim()
    .toLocaleLowerCase()
    .replace(/[\s·•・,，.。()（）[\]【】]/gu, "");
}

function organizationLabel(organization) {
  const lineage = Array.isArray(organization.lineage_names)
    ? organization.lineage_names.join(" / ")
    : organization.canonical_name;
  return `${lineage} · ${organization.id}`;
}

function parseOrganizationInput(input, allowedIds = null) {
  if (input.selectedId && state.organizationById.has(input.selectedId)) {
    if (!allowedIds || allowedIds.has(input.selectedId)) {
      return input.selectedId;
    }
  }
  const raw = input.value.trim();
  const organizationId = state.organizationIdByLabel.get(raw) || raw;
  if (!state.organizationById.has(organizationId)) {
    return null;
  }
  if (allowedIds && !allowedIds.has(organizationId)) {
    return null;
  }
  return organizationId;
}

function organizationsForLevel(level, parentId) {
  const allowedTypes = new Set(LEVEL_TYPES[level]);
  return state.manifest.organizations.filter(
    (organization) =>
      allowedTypes.has(organization.type) && (organization.parent_id || null) === parentId,
  );
}

function findExactOrganization(level, parentId, submittedName) {
  const key = normalizeOrganizationName(submittedName);
  if (!key) {
    return null;
  }
  const matches = organizationsForLevel(level, parentId).filter((organization) => {
    const names = [organization.canonical_name, ...(organization.aliases || [])];
    return names.some((name) => normalizeOrganizationName(name) === key);
  });
  return matches.length === 1 ? matches[0].id : null;
}

function suggestedOrganizations(group) {
  const result = new Map();
  const target = state.organizationById.get(group.suggested_organization_id);
  if (!target) {
    return result;
  }
  for (const organizationId of target.lineage_ids || []) {
    const organization = state.organizationById.get(organizationId);
    if (!organization) {
      continue;
    }
    if (organization.type === "university") {
      result.set("university", organization.id);
    } else if (["school", "institute"].includes(organization.type)) {
      result.set("school", organization.id);
    } else {
      result.set("department", organization.id);
    }
  }
  return result;
}

async function sha256Hex(value) {
  const bytes = value instanceof ArrayBuffer ? value : new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function proposedOrganizationId(type, canonicalName, parentId) {
  const seed = `${type}\n${normalizeOrganizationName(canonicalName)}\n${parentId || ""}`;
  return `org_auto_${(await sha256Hex(seed)).slice(0, 20)}`;
}

function closeActiveFloatingControl() {
  const current = activeFloatingControl;
  activeFloatingControl = null;
  if (current) {
    current.close();
  }
}

function activateFloatingControl(root, popup, close) {
  if (activeFloatingControl?.root !== root) {
    closeActiveFloatingControl();
  }
  activeFloatingControl = { root, popup, close };
}

function deactivateFloatingControl(root) {
  if (activeFloatingControl?.root === root) {
    activeFloatingControl = null;
  }
}

function positionFloatingMenu(root, menu, minimumWidth = 0) {
  const margin = 8;
  const gap = 6;
  const rect = root.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  const width = Math.min(
    Math.max(rect.width, minimumWidth),
    Math.max(0, viewportWidth - margin * 2),
  );
  const left = Math.min(Math.max(margin, rect.left), viewportWidth - margin - width);
  const spaceBelow = viewportHeight - rect.bottom - margin - gap;
  const spaceAbove = rect.top - margin - gap;
  const openAbove = spaceBelow < 160 && spaceAbove > spaceBelow;
  const availableHeight = Math.max(96, openAbove ? spaceAbove : spaceBelow);

  menu.style.left = `${left}px`;
  menu.style.width = `${width}px`;
  menu.style.maxHeight = `${Math.min(280, availableHeight)}px`;
  const menuHeight = Math.min(menu.scrollHeight, 280, availableHeight);
  menu.style.top = `${openAbove ? Math.max(margin, rect.top - gap - menuHeight) : rect.bottom + gap}px`;
}

document.addEventListener("pointerdown", (event) => {
  if (
    activeFloatingControl &&
    !activeFloatingControl.root.contains(event.target) &&
    !activeFloatingControl.popup.contains(event.target)
  ) {
    closeActiveFloatingControl();
  }
});
window.addEventListener("resize", closeActiveFloatingControl);
window.addEventListener("scroll", closeActiveFloatingControl, true);

function createSelect(options, className, ariaLabel) {
  const root = element("div", `custom-select ${className}`);
  const trigger = element("button", "custom-select-trigger");
  const valueLabel = element("span", "custom-select-value");
  const chevron = element("span", "select-chevron");
  const menu = element("div", "floating-menu custom-select-menu");
  const menuId = `custom-select-options-${++floatingControlSequence}`;
  let selectedValue = options[0]?.[0] || "";
  let highlightedIndex = 0;
  let open = false;
  const optionNodes = [];

  trigger.type = "button";
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", menuId);
  trigger.setAttribute("aria-label", ariaLabel);
  chevron.setAttribute("aria-hidden", "true");
  trigger.append(valueLabel, chevron);
  menu.id = menuId;
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", ariaLabel);
  menu.hidden = true;

  function updateHighlight() {
    for (const [index, option] of optionNodes.entries()) {
      option.classList.toggle("is-highlighted", index === highlightedIndex);
    }
    const highlighted = optionNodes[highlightedIndex];
    if (highlighted) {
      trigger.setAttribute("aria-activedescendant", highlighted.id);
    } else {
      trigger.removeAttribute("aria-activedescendant");
    }
  }

  function setValue(value, notify = false) {
    const index = options.findIndex(([candidate]) => candidate === value);
    if (index < 0) {
      return;
    }
    const changed = selectedValue !== value;
    selectedValue = value;
    highlightedIndex = index;
    root.dataset.value = value;
    valueLabel.textContent = options[index][1];
    for (const [optionIndex, option] of optionNodes.entries()) {
      const selected = optionIndex === index;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-selected", String(selected));
    }
    updateHighlight();
    if (notify && changed) {
      root.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function closeMenu() {
    open = false;
    root.removeAttribute("data-open");
    trigger.setAttribute("aria-expanded", "false");
    trigger.removeAttribute("aria-activedescendant");
    menu.hidden = true;
    deactivateFloatingControl(root);
  }

  function openMenu() {
    if (trigger.disabled || open) {
      return;
    }
    activateFloatingControl(root, menu, closeMenu);
    open = true;
    root.dataset.open = "true";
    trigger.setAttribute("aria-expanded", "true");
    menu.hidden = false;
    highlightedIndex = Math.max(
      0,
      options.findIndex(([candidate]) => candidate === selectedValue),
    );
    updateHighlight();
    positionFloatingMenu(root, menu);
  }

  for (const [index, [value, label]] of options.entries()) {
    const option = element("button", "custom-select-option", label);
    option.id = `${menuId}-option-${index + 1}`;
    option.type = "button";
    option.tabIndex = -1;
    option.setAttribute("role", "option");
    option.addEventListener("pointerenter", () => {
      highlightedIndex = index;
      updateHighlight();
    });
    option.addEventListener("click", () => {
      setValue(value, true);
      closeMenu();
      trigger.focus();
    });
    optionNodes.push(option);
    menu.append(option);
  }

  trigger.addEventListener("click", () => {
    if (open) {
      closeMenu();
    } else {
      openMenu();
    }
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeMenu();
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      if (event.key === "Home") {
        highlightedIndex = 0;
      } else if (event.key === "End") {
        highlightedIndex = optionNodes.length - 1;
      } else {
        const direction = event.key === "ArrowDown" ? 1 : -1;
        highlightedIndex =
          (highlightedIndex + direction + optionNodes.length) % optionNodes.length;
      }
      updateHighlight();
      optionNodes[highlightedIndex]?.scrollIntoView({ block: "nearest" });
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && open) {
      event.preventDefault();
      const selected = options[highlightedIndex];
      if (selected) {
        setValue(selected[0], true);
        closeMenu();
      }
    }
  });

  Object.defineProperty(root, "value", {
    get: () => selectedValue,
    set: (value) => setValue(String(value)),
  });
  Object.defineProperty(root, "disabled", {
    get: () => trigger.disabled,
    set: (value) => {
      trigger.disabled = Boolean(value);
      root.classList.toggle("is-disabled", trigger.disabled);
      if (trigger.disabled) {
        closeMenu();
      }
    },
  });

  root.append(trigger);
  document.body.append(menu);
  setValue(selectedValue);
  return root;
}

function createOrganizationPicker(organizations, placeholder, ariaLabel) {
  const root = element("div", "organization-picker organization-input");
  const input = element("input", "organization-picker-input");
  const toggle = element("button", "organization-picker-toggle");
  const chevron = element("span", "select-chevron");
  const menu = element("div", "floating-menu organization-picker-menu");
  const menuId = `organization-picker-options-${++floatingControlSequence}`;
  let availableOrganizations = [...organizations];
  let visibleOrganizations = [];
  let selectedId = null;
  let highlightedIndex = -1;
  let open = false;

  input.type = "search";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", menuId);
  input.setAttribute("aria-label", ariaLabel);
  toggle.type = "button";
  toggle.setAttribute("aria-label", `${ariaLabel}：展开选项`);
  toggle.tabIndex = -1;
  chevron.setAttribute("aria-hidden", "true");
  toggle.append(chevron);
  menu.id = menuId;
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", ariaLabel);
  menu.hidden = true;

  function updateHighlight() {
    const optionNodes = [...menu.querySelectorAll(".organization-picker-option")];
    for (const [index, option] of optionNodes.entries()) {
      option.classList.toggle("is-highlighted", index === highlightedIndex);
    }
    const highlighted = optionNodes[highlightedIndex];
    if (highlighted) {
      input.setAttribute("aria-activedescendant", highlighted.id);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function selectOrganization(organization, notify = true) {
    const changed = selectedId !== organization.id;
    selectedId = organization.id;
    input.value = state.organizationLabelById.get(organization.id) || organization.canonical_name;
    if (notify && changed) {
      root.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function renderOptions() {
    const query = input.value.trim().toLocaleLowerCase();
    visibleOrganizations = availableOrganizations
      .filter((organization) => {
        if (!query || selectedId === organization.id) {
          return true;
        }
        const searchText = [
          organizationLabel(organization),
          ...(organization.aliases || []),
        ]
          .join(" ")
          .toLocaleLowerCase();
        return searchText.includes(query);
      })
      .slice(0, 80);
    menu.replaceChildren();
    highlightedIndex = visibleOrganizations.length ? 0 : -1;
    if (!visibleOrganizations.length) {
      menu.append(element("div", "picker-empty", "没有匹配的现有机构"));
      updateHighlight();
      return;
    }
    for (const [index, organization] of visibleOrganizations.entries()) {
      const option = element("button", "organization-picker-option");
      const primary = element("span", "organization-option-name", organization.canonical_name);
      const lineage = (organization.lineage_names || [organization.canonical_name]).join(" / ");
      const secondary = element(
        "span",
        "organization-option-path",
        `${lineage} · ${organization.id}`,
      );
      option.id = `${menuId}-option-${index + 1}`;
      option.type = "button";
      option.tabIndex = -1;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(selectedId === organization.id));
      option.append(primary, secondary);
      option.addEventListener("pointerenter", () => {
        highlightedIndex = index;
        updateHighlight();
      });
      option.addEventListener("click", () => {
        selectOrganization(organization);
        closeMenu();
        input.focus();
      });
      menu.append(option);
    }
    updateHighlight();
  }

  function closeMenu() {
    open = false;
    root.removeAttribute("data-open");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    menu.hidden = true;
    deactivateFloatingControl(root);
  }

  function openMenu() {
    if (open) {
      return;
    }
    activateFloatingControl(root, menu, closeMenu);
    open = true;
    root.dataset.open = "true";
    input.setAttribute("aria-expanded", "true");
    renderOptions();
    menu.hidden = false;
    positionFloatingMenu(root, menu, 280);
  }

  input.addEventListener("focus", openMenu);
  input.addEventListener("click", openMenu);
  input.addEventListener("input", () => {
    selectedId = null;
    if (!open) {
      openMenu();
    } else {
      renderOptions();
      positionFloatingMenu(root, menu, 280);
    }
    root.dispatchEvent(new Event("input", { bubbles: true }));
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeMenu();
      return;
    }
    if (event.key === "Tab") {
      closeMenu();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      if (visibleOrganizations.length) {
        const direction = event.key === "ArrowDown" ? 1 : -1;
        highlightedIndex =
          (highlightedIndex + direction + visibleOrganizations.length) %
          visibleOrganizations.length;
        updateHighlight();
        menu
          .querySelectorAll(".organization-picker-option")
          [highlightedIndex]?.scrollIntoView({ block: "nearest" });
      }
      return;
    }
    if (event.key === "Enter" && open && highlightedIndex >= 0) {
      event.preventDefault();
      selectOrganization(visibleOrganizations[highlightedIndex]);
      closeMenu();
    }
  });
  toggle.addEventListener("click", () => {
    const wasOpen = open;
    input.focus();
    if (wasOpen) {
      closeMenu();
    } else if (!open) {
      openMenu();
    }
  });

  Object.defineProperty(root, "value", {
    get: () => input.value,
    set: (value) => {
      input.value = String(value || "");
      selectedId = state.organizationIdByLabel.get(input.value) || null;
    },
  });
  Object.defineProperty(root, "selectedId", {
    get: () => selectedId,
  });
  root.setOptions = (nextOrganizations) => {
    availableOrganizations = [...nextOrganizations];
    if (selectedId && !availableOrganizations.some(({ id }) => id === selectedId)) {
      selectedId = null;
      input.value = "";
    }
    if (open) {
      renderOptions();
      positionFloatingMenu(root, menu, 280);
    }
  };

  root.append(input, toggle);
  document.body.append(menu);
  return root;
}

function createInput(type, placeholder, className) {
  const input = element("input", className);
  input.type = type;
  input.placeholder = placeholder;
  input.autocomplete = "off";
  return input;
}

function renderInvalidRows() {
  const rows = state.manifest.invalid_rows;
  if (!rows.length) {
    return;
  }
  nodes.invalidPanel.hidden = false;
  for (const row of rows) {
    const item = element("div", "invalid-row");
    const title = element("strong", null, `表格第 ${row.sheet_row} 行`);
    const reason = element("span", null, row.message);
    item.append(title, reason);
    nodes.invalidRows.append(item);
  }
}

function pathText(group) {
  return LEVELS.map((level) => group.submitted[level]).filter(Boolean).join(" / ") || "未填写学校";
}

function sourceLinks(group) {
  const container = element("div", "source-links");
  for (const sourceUrl of group.source_urls.slice(0, 5)) {
    const link = element("a", null, new URL(sourceUrl).hostname);
    link.href = sourceUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    container.append(link);
  }
  if (group.source_urls.length > 5) {
    container.append(element("span", null, `另有 ${group.source_urls.length - 5} 个来源`));
  }
  return container;
}

function createLevelEditor(group, level, suggestedId) {
  const submittedName = group.submitted[level] || "";
  const wrapper = element("section", "level-editor");
  const heading = element("div", "level-heading");
  const title = element("h4", null, LEVEL_LABELS[level]);
  const submitted = element("span", "submitted-name", submittedName || "未填写");
  heading.append(title, submitted);

  const actionOptions = [
    ["existing", "映射到现有机构"],
    ["create", "新建机构"],
  ];
  if (level !== "university") {
    actionOptions.push(["skip", "归到上级（不建此层）"]);
  }
  const action = createSelect(
    actionOptions,
    "level-action",
    `${LEVEL_LABELS[level]}处理方式`,
  );

  const existingPanel = element("div", "editor-panel existing-panel");
  const existingInput = createOrganizationPicker(
    [],
    "搜索并选择现有机构",
    `选择现有${LEVEL_LABELS[level]}`,
  );
  existingPanel.append(existingInput);

  const createPanel = element("div", "editor-panel create-panel");
  const organizationType = createSelect(
    LEVEL_TYPES[level].map((value) => [value, TYPE_LABELS[value]]),
    "organization-type",
    `${LEVEL_LABELS[level]}类型`,
  );
  const canonicalName = createInput("text", "正式名称", "canonical-name");
  canonicalName.setAttribute("aria-label", `${LEVEL_LABELS[level]}正式名称`);
  canonicalName.maxLength = 255;
  canonicalName.value = submittedName;
  const officialUrl = createInput("url", "http:// 或 https:// 官方网站", "official-url");
  officialUrl.setAttribute("aria-label", `${LEVEL_LABELS[level]}官方网站`);
  officialUrl.maxLength = 500;
  const approvedDomains = createInput(
    "text",
    "批准域名，多个用逗号分隔",
    "approved-domains",
  );
  approvedDomains.setAttribute("aria-label", `${LEVEL_LABELS[level]}批准域名`);
  approvedDomains.maxLength = 2000;
  if (level === "university") {
    approvedDomains.value = group.source_domains.join(", ");
  }
  createPanel.append(organizationType, canonicalName, officialUrl, approvedDomains);

  const aliasLabel = element("label", "alias-option");
  const saveAlias = document.createElement("input");
  saveAlias.type = "checkbox";
  saveAlias.setAttribute("aria-label", `保存${submittedName || LEVEL_LABELS[level]}为别名`);
  const aliasText = element("span", null, "把投稿写法保存为别名");
  aliasLabel.append(saveAlias, aliasText);

  let initialExistingId = suggestedId;
  if (!initialExistingId && level === "university") {
    initialExistingId = findExactOrganization(level, null, submittedName);
  }
  if (initialExistingId) {
    action.value = "existing";
    existingInput.value = state.organizationLabelById.get(initialExistingId);
  } else if (!submittedName && level !== "university") {
    action.value = "skip";
  } else {
    action.value = "create";
    saveAlias.checked = Boolean(submittedName);
  }

  wrapper.append(heading, action, existingPanel, createPanel, aliasLabel);
  return {
    level,
    submittedName,
    wrapper,
    action,
    existingPanel,
    existingInput,
    allowedExistingIds: new Set(),
    createPanel,
    organizationType,
    canonicalName,
    officialUrl,
    approvedDomains,
    aliasLabel,
    saveAlias,
  };
}

function createRowEditor(row) {
  const wrapper = element("div", "row-editor");
  const identity = element("div", "row-identity");
  identity.append(element("strong", null, row.name), element("span", null, row.email));
  const action = createSelect(
    [
      ["follow", "跟随分组"],
      ["map_existing", "映射到其他现有机构"],
      ["reject", "拒绝此行"],
    ],
    "row-action",
    `${row.name}的逐行处理方式`,
  );
  const organizationInput = createOrganizationPicker(
    state.manifest.organizations,
    "搜索并选择其他机构",
    `${row.name}改映射到的机构`,
  );
  const reason = createInput("text", "拒绝原因", "row-reason");
  reason.setAttribute("aria-label", `拒绝${row.name}的原因`);
  reason.maxLength = 500;
  organizationInput.hidden = true;
  reason.hidden = true;
  action.addEventListener("change", () => {
    organizationInput.hidden = action.value !== "map_existing";
    reason.hidden = action.value !== "reject";
  });
  wrapper.append(identity, action, organizationInput, reason);
  return { row, wrapper, action, organizationInput, reason };
}

async function updateCard(card) {
  const token = ++card.updateToken;
  const rejecting = card.groupAction.value === "reject";
  card.groupReason.hidden = !rejecting;
  card.levelsContainer.hidden = rejecting;
  if (rejecting) {
    return;
  }

  let parentId = null;
  let skippedSchool = false;
  for (const editor of card.levelEditors) {
    if (editor.level === "department" && skippedSchool) {
      editor.action.value = "skip";
      editor.action.disabled = true;
    } else {
      editor.action.disabled = false;
    }

    const available = organizationsForLevel(editor.level, parentId);
    editor.allowedExistingIds = new Set(available.map((organization) => organization.id));
    editor.existingInput.setOptions(available);
    const selectedId = parseOrganizationInput(editor.existingInput);
    if (selectedId && !editor.allowedExistingIds.has(selectedId)) {
      editor.existingInput.value = "";
    }

    const action = editor.action.value;
    editor.existingPanel.hidden = action !== "existing";
    editor.createPanel.hidden = action !== "create";
    editor.aliasLabel.hidden = action === "skip" || !editor.submittedName;
    if (action === "skip") {
      editor.saveAlias.checked = false;
      if (editor.level === "school") {
        skippedSchool = true;
      }
      continue;
    }
    if (action === "existing") {
      parentId = parseOrganizationInput(editor.existingInput, editor.allowedExistingIds);
      continue;
    }
    const canonicalName = editor.canonicalName.value.trim();
    if (!canonicalName) {
      parentId = null;
      continue;
    }
    parentId = await proposedOrganizationId(
      editor.organizationType.value,
      canonicalName,
      parentId,
    );
    if (token !== card.updateToken) {
      return;
    }
  }
}

function createGroupCard(group, index) {
  const article = element("article", "review-group");
  const header = element("div", "group-header");
  const titleArea = element("div");
  titleArea.append(
    element("span", "group-index", `组合 ${index + 1}`),
    element("h3", null, pathText(group)),
  );
  const badges = element("div", "group-badges");
  badges.append(
    element("span", "badge", `${group.rows.length} 位导师`),
    element("span", "badge neutral-badge", `${group.source_domains.length} 个域名`),
  );
  header.append(titleArea, badges);

  const sources = sourceLinks(group);
  const groupControls = element("div", "group-controls");
  const groupAction = createSelect(
    [
      ["resolve", "确认并映射"],
      ["reject", "拒绝整个组合"],
    ],
    "group-action",
    `${pathText(group)}的分组处理方式`,
  );
  const groupReason = createInput("text", "拒绝原因", "group-reason");
  groupReason.setAttribute("aria-label", `拒绝${pathText(group)}的原因`);
  groupReason.maxLength = 500;
  groupReason.hidden = true;
  groupControls.append(groupAction, groupReason);

  const suggested = suggestedOrganizations(group);
  const levelsContainer = element("div", "levels-container");
  const levelEditors = LEVELS.map((level) =>
    createLevelEditor(group, level, suggested.get(level) || null),
  );
  for (const editor of levelEditors) {
    levelsContainer.append(editor.wrapper);
  }

  const rowsDetails = element("details", "rows-details");
  const rowsSummary = element("summary", null, "拆分或拒绝个别导师行");
  const rowsContainer = element("div", "rows-container");
  const rowEditors = group.rows.map(createRowEditor);
  for (const editor of rowEditors) {
    rowsContainer.append(editor.wrapper);
  }
  rowsDetails.append(rowsSummary, rowsContainer);
  article.append(header, sources, groupControls, levelsContainer, rowsDetails);

  const card = {
    group,
    article,
    groupAction,
    groupReason,
    levelsContainer,
    levelEditors,
    rowEditors,
    updateToken: 0,
  };
  groupAction.addEventListener("change", () => void updateCard(card));
  for (const editor of levelEditors) {
    for (const control of [
      editor.action,
      editor.existingInput,
      editor.organizationType,
      editor.canonicalName,
    ]) {
      control.addEventListener("change", () => void updateCard(card));
    }
  }
  void updateCard(card);
  return card;
}

function validateWebUrl(value, label) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label}必须是完整的 HTTP 或 HTTPS URL`);
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    (parsed.port &&
      !(
        (parsed.protocol === "http:" && parsed.port === "80") ||
        (parsed.protocol === "https:" && parsed.port === "443")
      ))
  ) {
    throw new Error(`${label}必须是安全的 HTTP 或 HTTPS URL`);
  }
  return value;
}

function parseDomains(value) {
  const domains = [
    ...new Set(
      value
        .split(/[,，;；\s]+/u)
        .map((item) => item.trim().toLowerCase().replace(/\.$/u, ""))
        .filter(Boolean),
    ),
  ];
  const invalid = domains.find((domain) => !DOMAIN_PATTERN.test(domain));
  if (invalid) {
    throw new Error(`批准域名格式无效：${invalid}`);
  }
  return domains;
}

function hostMatchesDomain(hostname, domain) {
  return hostname === domain || hostname.endsWith(`.${domain}`);
}

async function collectLevels(card) {
  const levels = [];
  let parentId = null;
  let inheritedDomains = [];
  let skippedSchool = false;
  for (const editor of card.levelEditors) {
    const action = editor.action.value;
    const prefix = `${pathText(card.group)}的${LEVEL_LABELS[editor.level]}`;
    if (action === "skip") {
      if (editor.level === "university") {
        throw new Error(`${prefix}不能跳过`);
      }
      if (editor.level === "school") {
        skippedSchool = true;
      }
      levels.push({
        level: editor.level,
        action,
        organization_id: null,
        organization_type: null,
        canonical_name: null,
        official_url: null,
        approved_domains: [],
        save_submitted_as_alias: false,
      });
      continue;
    }
    if (skippedSchool) {
      throw new Error(`${prefix}不能在跳过学院后单独建立`);
    }
    if (action === "existing") {
      const organizationId = parseOrganizationInput(
        editor.existingInput,
        editor.allowedExistingIds,
      );
      if (!organizationId) {
        throw new Error(`${prefix}需要从建议列表中选择现有机构`);
      }
      levels.push({
        level: editor.level,
        action,
        organization_id: organizationId,
        organization_type: null,
        canonical_name: null,
        official_url: null,
        approved_domains: [],
        save_submitted_as_alias: editor.saveAlias.checked,
      });
      parentId = organizationId;
      inheritedDomains = state.organizationById.get(organizationId).approved_domains || [];
      continue;
    }
    const canonicalName = editor.canonicalName.value.trim();
    if (!canonicalName) {
      throw new Error(`${prefix}需要填写正式名称`);
    }
    const officialUrl = validateWebUrl(editor.officialUrl.value.trim(), `${prefix}官网`);
    const approvedDomains = parseDomains(editor.approvedDomains.value);
    if (editor.level === "university" && approvedDomains.length === 0) {
      throw new Error(`${prefix}至少需要一个批准域名`);
    }
    const effectiveDomains = [...new Set([...inheritedDomains, ...approvedDomains])];
    const officialHostname = new URL(officialUrl).hostname.toLowerCase().replace(/\.$/u, "");
    if (!effectiveDomains.some((domain) => hostMatchesDomain(officialHostname, domain))) {
      throw new Error(`${prefix}官网不属于本级或上级批准域名`);
    }
    const organizationType = editor.organizationType.value;
    levels.push({
      level: editor.level,
      action,
      organization_id: null,
      organization_type: organizationType,
      canonical_name: canonicalName,
      official_url: officialUrl,
      approved_domains: approvedDomains,
      save_submitted_as_alias: editor.saveAlias.checked,
    });
    parentId = await proposedOrganizationId(organizationType, canonicalName, parentId);
    inheritedDomains = effectiveDomains;
  }
  return levels;
}

function collectRowOverrides(card) {
  const overrides = [];
  for (const editor of card.rowEditors) {
    if (editor.action.value === "follow") {
      continue;
    }
    if (editor.action.value === "map_existing") {
      const organizationId = parseOrganizationInput(editor.organizationInput);
      if (!organizationId) {
        throw new Error(`${editor.row.name}需要从建议列表中选择机构`);
      }
      overrides.push({
        proposal_id: editor.row.proposal_id,
        action: "map_existing",
        organization_id: organizationId,
        reason: null,
      });
      continue;
    }
    const reason = editor.reason.value.trim();
    if (!reason) {
      throw new Error(`拒绝${editor.row.name}时需要填写原因`);
    }
    overrides.push({
      proposal_id: editor.row.proposal_id,
      action: "reject",
      organization_id: null,
      reason,
    });
  }
  return overrides;
}

async function collectDecision() {
  const decisions = [];
  for (const card of state.cards) {
    const rowOverrides = collectRowOverrides(card);
    if (card.groupAction.value === "reject") {
      const reason = card.groupReason.value.trim();
      if (!reason) {
        throw new Error(`拒绝${pathText(card.group)}时需要填写原因`);
      }
      decisions.push({
        group_id: card.group.id,
        action: "reject",
        reason,
        levels: [],
        row_overrides: rowOverrides,
      });
      continue;
    }
    decisions.push({
      group_id: card.group.id,
      action: "resolve",
      reason: null,
      levels: await collectLevels(card),
      row_overrides: rowOverrides,
    });
  }
  return {
    schema_version: 1,
    kind: "batch_organization_review_decision",
    pull_request_number: state.pullNumber,
    issue_number: state.issueNumber,
    manifest_sha256: state.manifestSha256,
    decisions,
  };
}

async function generateDecision() {
  nodes.decisionError.hidden = true;
  nodes.decisionOutput.hidden = true;
  nodes.copyStatus.textContent = "";
  try {
    const decision = await collectDecision();
    const body = `${COMMENT_MARKER}\n\`\`\`json\n${JSON.stringify(decision, null, 2)}\n\`\`\``;
    if (new TextEncoder().encode(body).length > MAX_COMMENT_BYTES) {
      throw new Error("审核评论超过 GitHub 处理上限，请减少逐行拆分后再生成");
    }
    nodes.decisionText.value = body;
    nodes.decisionOutput.hidden = false;
    nodes.decisionOutput.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    nodes.decisionError.textContent = error instanceof Error ? error.message : "无法生成审核评论";
    nodes.decisionError.hidden = false;
  }
}

async function copyAndOpenPullRequest() {
  const pullWindow = window.open(state.pullUrl, "_blank", "noopener,noreferrer");
  let copied = false;
  try {
    await navigator.clipboard.writeText(nodes.decisionText.value);
    copied = true;
  } catch {
    nodes.decisionText.focus();
    nodes.decisionText.select();
    copied = document.execCommand("copy");
  }
  nodes.copyStatus.textContent = copied
    ? "已复制。请在刚打开的 PR 中粘贴并发表评论。"
    : "浏览器未允许自动复制，请手动复制文本框内容。";
  if (!pullWindow) {
    nodes.copyStatus.textContent += " 浏览器拦截了新窗口，请手动打开 PR。";
  }
}

function validateManifest(manifest, issueNumber) {
  if (
    !manifest ||
    manifest.kind !== "batch_organization_review" ||
    manifest.schema_version !== 1 ||
    manifest.issue?.number !== issueNumber ||
    manifest.proposal_directory !== `proposals/batch-issue-${issueNumber}` ||
    !Array.isArray(manifest.groups) ||
    !Array.isArray(manifest.invalid_rows) ||
    !Array.isArray(manifest.organizations)
  ) {
    throw new Error("PR 中的机构审核清单格式或 Issue 归属不正确");
  }
}

async function loadReview() {
  try {
    const parameter = new URLSearchParams(window.location.search).get("pr") || "";
    if (!/^[1-9][0-9]*$/.test(parameter)) {
      throw new Error("链接缺少有效的 PR 编号");
    }
    const pullNumber = Number(parameter);
    if (!Number.isSafeInteger(pullNumber)) {
      throw new Error("PR 编号超出支持范围");
    }
    const pullResponse = await fetch(
      `https://api.github.com/repos/${REPOSITORY}/pulls/${pullNumber}`,
      { cache: "no-store" },
    );
    if (!pullResponse.ok) {
      throw new Error(`无法读取 PR（GitHub 返回 ${pullResponse.status}）`);
    }
    const pull = await pullResponse.json();
    const branchMatch = typeof pull.head?.ref === "string" ? BRANCH_PATTERN.exec(pull.head.ref) : null;
    if (
      pull.number !== pullNumber ||
      pull.state !== "open" ||
      pull.base?.ref !== "main" ||
      pull.head?.repo?.full_name?.toLocaleLowerCase() !== REPOSITORY.toLocaleLowerCase() ||
      !branchMatch ||
      !SHA_PATTERN.test(pull.head?.sha || "")
    ) {
      throw new Error("该 PR 不是开放的内部批量审核分支");
    }
    const issueNumber = Number(branchMatch[1]);
    const manifestUrl =
      `https://raw.githubusercontent.com/${REPOSITORY}/${pull.head.sha}` +
      `/reviews/pending/batch-issue-${issueNumber}.json`;
    const manifestResponse = await fetch(manifestUrl, { cache: "no-store" });
    if (!manifestResponse.ok) {
      throw new Error(`无法读取审核清单（GitHub 返回 ${manifestResponse.status}）`);
    }
    const manifestBuffer = await manifestResponse.arrayBuffer();
    if (manifestBuffer.byteLength === 0 || manifestBuffer.byteLength > MAX_MANIFEST_BYTES) {
      throw new Error("审核清单为空或超过页面处理上限");
    }
    const manifest = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(manifestBuffer));
    validateManifest(manifest, issueNumber);

    state.pullNumber = pullNumber;
    state.pullUrl = `https://github.com/${REPOSITORY}/pull/${pullNumber}`;
    state.issueNumber = issueNumber;
    state.manifest = manifest;
    state.manifestSha256 = await sha256Hex(manifestBuffer);
    for (const organization of manifest.organizations) {
      state.organizationById.set(organization.id, organization);
      const label = organizationLabel(organization);
      state.organizationLabelById.set(organization.id, label);
      state.organizationIdByLabel.set(label, organization.id);
    }

    const rowCount = manifest.groups.reduce((total, group) => total + group.rows.length, 0);
    nodes.issue.textContent = `#${issueNumber}`;
    nodes.groupCount.textContent = String(manifest.groups.length);
    nodes.rowCount.textContent = String(rowCount);
    nodes.invalidCount.textContent = String(manifest.invalid_rows.length);
    nodes.summary.hidden = false;
    renderInvalidRows();

    if (manifest.groups.length) {
      nodes.groupsSection.hidden = false;
      for (const [index, group] of manifest.groups.entries()) {
        const card = createGroupCard(group, index);
        state.cards.push(card);
        nodes.groups.append(card.article);
      }
    } else {
      nodes.emptyReview.hidden = false;
    }
    nodes.decisionPanel.hidden = false;
    setStatus("ready", "审核清单已验证", `正在审核 PR #${pullNumber}，提交结果前仍会由后端复核。`);
  } catch (error) {
    setStatus("error", "无法打开审核清单", error instanceof Error ? error.message : "未知错误");
  }
}

nodes.generate.addEventListener("click", () => void generateDecision());
nodes.copyOpen.addEventListener("click", () => void copyAndOpenPullRequest());
void loadReview();
