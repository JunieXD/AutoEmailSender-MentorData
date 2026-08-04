const REPOSITORY = "JunieXD/AutoEmailSender-MentorData";
const COMMENT_MARKER = "<!-- mentor-data-organization-review:v1 -->";
const BRANCH_PATTERN = /^batch\/issue-([1-9][0-9]*)$/;
const SHA_PATTERN = /^[a-f0-9]{40,64}$/;
const DOMAIN_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;
const MAX_MANIFEST_BYTES = 20_000_000;
const MAX_COMMENT_BYTES = 200_000;
const MAX_PROPOSAL_BYTES = 500_000;
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
  pullHeadSha: null,
  issueNumber: null,
  manifest: null,
  manifestSha256: null,
  organizationById: new Map(),
  organizationsByLevelParent: new Map(),
  organizationIdsByExactName: new Map(),
  selectableOrganizationById: new Map(),
  organizationLabelById: new Map(),
  organizationIdByLabel: new Map(),
  profileUrlByProposalId: new Map(),
  organizationDrafts: [],
  organizationDraftByKey: new Map(),
  pendingOrganizationIds: new Set(),
  pendingOrganizations: [],
  pendingOrganizationsSignature: "",
  rowEditors: [],
  rowEditorByProposalId: new Map(),
  restoredRowValues: new Map(),
  groupCardById: new Map(),
  storageKey: null,
  updateToken: 0,
  autosaveDirty: false,
  proposedOrganizationIdCache: new Map(),
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
  organizationsSection: document.querySelector("#organizations-section"),
  organizationTree: document.querySelector("#organization-tree"),
  organizationProgress: document.querySelector("#organization-progress"),
  nextPending: document.querySelector("#next-pending"),
  autosaveStatus: document.querySelector("#autosave-status"),
  groupsSection: document.querySelector("#groups-section"),
  groups: document.querySelector("#review-groups"),
  emptyReview: document.querySelector("#empty-review"),
  decisionPanel: document.querySelector("#decision-panel"),
  generate: document.querySelector("#generate-decision"),
  decisionError: document.querySelector("#decision-error"),
  decisionPreview: document.querySelector("#decision-preview"),
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
  return organization.pending ? `${lineage} · 本次新建` : `${lineage} · ${organization.id}`;
}

function parseOrganizationInput(input, allowedIds = null) {
  if (input.selectedId && state.selectableOrganizationById.has(input.selectedId)) {
    if (!allowedIds || allowedIds.has(input.selectedId)) {
      return input.selectedId;
    }
  }
  const raw = input.value.trim();
  const organizationId = state.organizationIdByLabel.get(raw) || raw;
  if (!state.selectableOrganizationById.has(organizationId)) {
    return null;
  }
  if (allowedIds && !allowedIds.has(organizationId)) {
    return null;
  }
  return organizationId;
}

function organizationLevel(organizationType) {
  if (organizationType === "university") {
    return "university";
  }
  if (["school", "institute"].includes(organizationType)) {
    return "school";
  }
  return "department";
}

function levelParentKey(level, parentId) {
  return `${level}\u001f${parentId || "root"}`;
}

function organizationsForLevel(level, parentId) {
  return state.organizationsByLevelParent.get(levelParentKey(level, parentId)) || [];
}

function findExactOrganization(level, parentId, submittedName) {
  const key = normalizeOrganizationName(submittedName);
  if (!key) {
    return null;
  }
  const matches = state.organizationIdsByExactName.get(
    `${levelParentKey(level, parentId)}\u001f${key}`,
  );
  return matches?.length === 1 ? matches[0] : null;
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
  let pending = state.proposedOrganizationIdCache.get(seed);
  if (!pending) {
    pending = sha256Hex(seed).then((digest) => `org_auto_${digest.slice(0, 20)}`);
    state.proposedOrganizationIdCache.set(seed, pending);
  }
  return pending;
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
        organization.pending ? `${lineage} · 本次新建` : `${lineage} · ${organization.id}`,
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
    } else if (selectedId) {
      input.value = state.organizationLabelById.get(selectedId) || input.value;
    }
    if (open) {
      renderOptions();
      positionFloatingMenu(root, menu, 280);
    }
  };
  root.selectById = (organizationId, notify = false) => {
    const organization = availableOrganizations.find(({ id }) => id === organizationId);
    if (!organization) {
      return false;
    }
    selectOrganization(organization, notify);
    return true;
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

function organizationDraftKey(level, parentKey, submittedName) {
  return `${level}\u001f${parentKey || "root"}\u001f${normalizeOrganizationName(submittedName)}`;
}

function buildOrganizationDrafts() {
  const draftByKey = new Map();
  for (const group of state.manifest.groups) {
    const suggested = suggestedOrganizations(group);
    group.draftKeys = {};
    let parentKey = null;
    let missingSchool = false;
    for (const level of LEVELS) {
      const submittedName = String(group.submitted[level] || "").trim();
      if (!submittedName || (level === "department" && missingSchool)) {
        group.draftKeys[level] = null;
        if (level === "school") {
          missingSchool = true;
        }
        continue;
      }
      const key = organizationDraftKey(level, parentKey, submittedName);
      let draft = draftByKey.get(key);
      if (!draft) {
        draft = {
          key,
          level,
          submittedName,
          parentKey,
          childKeys: new Set(),
          groupIds: new Set(),
          sourceUrlCounts: new Map(),
          sourceDomains: new Set(),
          suggestedIds: new Set(),
          rowCount: 0,
          confirmed: false,
          initialized: false,
          targetId: null,
          effectiveDomains: [],
          lineageNames: [],
          forcedSkip: false,
          active: true,
          descendants: null,
          suggestedOfficialUrl: null,
          restoreExistingId: null,
          hasRestoredState: false,
          editor: null,
        };
        draftByKey.set(key, draft);
      }
      draft.groupIds.add(group.id);
      draft.rowCount += group.rows.length;
      for (const sourceUrl of group.source_urls) {
        draft.sourceUrlCounts.set(
          sourceUrl,
          (draft.sourceUrlCounts.get(sourceUrl) || 0) + group.rows.length,
        );
      }
      for (const domain of group.source_domains) {
        draft.sourceDomains.add(domain);
      }
      const suggestedId = suggested.get(level);
      if (suggestedId) {
        draft.suggestedIds.add(suggestedId);
      }
      group.draftKeys[level] = key;
      parentKey = key;
    }
  }

  for (const draft of draftByKey.values()) {
    if (draft.parentKey) {
      draftByKey.get(draft.parentKey)?.childKeys.add(draft.key);
    }
  }
  const collectDescendants = (draft) => {
    if (draft.descendants) {
      return draft.descendants;
    }
    const descendants = [];
    for (const childKey of draft.childKeys) {
      const child = draftByKey.get(childKey);
      if (child) {
        descendants.push(child, ...collectDescendants(child));
      }
    }
    draft.descendants = descendants;
    return descendants;
  };
  for (const draft of draftByKey.values()) {
    collectDescendants(draft);
  }
  state.organizationDraftByKey = draftByKey;
  return [...draftByKey.values()];
}

function suggestedOfficialUrl(draft) {
  if (draft.suggestedOfficialUrl !== null) {
    return draft.suggestedOfficialUrl;
  }
  const sources = [...draft.sourceUrlCounts.entries()].sort(
    ([firstUrl, firstCount], [secondUrl, secondCount]) =>
      secondCount - firstCount || firstUrl.localeCompare(secondUrl),
  );
  for (const [sourceUrl] of sources) {
    try {
      const parsed = new URL(sourceUrl);
      if (["http:", "https:"].includes(parsed.protocol)) {
        draft.suggestedOfficialUrl = `${parsed.origin}/`;
        return draft.suggestedOfficialUrl;
      }
    } catch {
      // The manifest is validated again before applying the decision.
    }
  }
  draft.suggestedOfficialUrl = "";
  return draft.suggestedOfficialUrl;
}

function labeledControl(labelText, control, hint = "") {
  const label = element("label", "review-field");
  label.append(element("span", "review-field-label", labelText), control);
  if (hint) {
    label.append(element("small", "review-field-hint", hint));
  }
  return label;
}

function createOrganizationDraftEditor(draft) {
  const details = element("details", `organization-draft organization-${draft.level}`);
  const summary = element("summary", "organization-draft-summary");
  const summaryIdentity = element("span", "organization-summary-identity");
  summaryIdentity.append(
    element("span", "organization-level-label", LEVEL_LABELS[draft.level]),
    element("strong", null, draft.submittedName),
  );
  const summaryMeta = element("span", "organization-summary-meta");
  const impact = element(
    "span",
    "organization-impact",
    `${draft.groupIds.size} 个分组 · ${draft.rowCount} 位导师`,
  );
  const status = element("span", "organization-draft-status", "待确认");
  summaryMeta.append(impact, status);
  summary.append(summaryIdentity, summaryMeta);

  const body = element("div", "organization-draft-body");
  const actionOptions = [
    ["existing", "使用现有机构"],
    ["create", "新建机构"],
  ];
  if (draft.level !== "university") {
    actionOptions.push(["skip", "归到上级（不建此层）"]);
  }
  const action = createSelect(
    actionOptions,
    "level-action",
    `${LEVEL_LABELS[draft.level]}处理方式`,
  );

  const existingPanel = element("div", "editor-panel existing-panel");
  const existingInput = createOrganizationPicker(
    [],
    "搜索并选择现有机构",
    `选择现有${LEVEL_LABELS[draft.level]}`,
  );
  existingPanel.append(labeledControl("现有机构", existingInput));

  const createPanel = element("div", "editor-panel create-panel");
  const organizationType = createSelect(
    LEVEL_TYPES[draft.level].map((value) => [value, TYPE_LABELS[value]]),
    "organization-type",
    `${LEVEL_LABELS[draft.level]}类型`,
  );
  const canonicalName = createInput("text", "正式名称", "canonical-name");
  canonicalName.setAttribute("aria-label", `${LEVEL_LABELS[draft.level]}正式名称`);
  canonicalName.maxLength = 255;
  canonicalName.value = draft.submittedName;
  const officialUrl = createInput(
    "url",
    draft.level === "university"
      ? "http:// 或 https:// 官方网站"
      : "官方网站（没有可以留空）",
    "official-url",
  );
  officialUrl.setAttribute("aria-label", `${LEVEL_LABELS[draft.level]}官方网站`);
  officialUrl.maxLength = 500;
  officialUrl.value = draft.level === "university" ? suggestedOfficialUrl(draft) : "";
  const approvedDomains = createInput(
    "text",
    "新增官方来源域名，多个用逗号分隔",
    "approved-domains",
  );
  approvedDomains.setAttribute("aria-label", `${LEVEL_LABELS[draft.level]}官方来源域名`);
  approvedDomains.maxLength = 2000;
  if (draft.level === "university") {
    approvedDomains.value = [...draft.sourceDomains].sort().join(", ");
  }
  createPanel.append(
    labeledControl("机构类型", organizationType),
    labeledControl("正式名称", canonicalName),
    labeledControl(
      draft.level === "university" ? "官方网站" : "官方网站（可留空）",
      officialUrl,
      officialUrl.value
        ? "已根据投稿来源自动生成，可修改。"
        : "没有独立主页时保持为空；需要时可从下方来源一键带入。",
    ),
    labeledControl(
      draft.level === "university" ? "官方来源域名" : "额外官方来源域名（可留空）",
      approvedDomains,
      draft.level === "university" ? "下级机构会自动继承。" : "默认继承上级，只填写额外域名。",
    ),
  );

  const sourceBar = element("div", "organization-source-bar");
  const sourceGroup = {
    source_urls: [...draft.sourceUrlCounts.keys()].sort(),
  };
  sourceBar.append(element("span", null, "投稿来源"), sourceLinks(sourceGroup));
  const restoreUrl = element(
    "button",
    "text-button",
    draft.level === "university" ? "恢复自动官网" : "使用来源网址",
  );
  restoreUrl.type = "button";
  restoreUrl.hidden = !suggestedOfficialUrl(draft);
  sourceBar.append(restoreUrl);

  const aliasLabel = element("label", "alias-option");
  const saveAlias = document.createElement("input");
  saveAlias.type = "checkbox";
  saveAlias.checked = Boolean(draft.submittedName);
  saveAlias.setAttribute("aria-label", `保存${draft.submittedName}为别名`);
  aliasLabel.append(saveAlias, element("span", null, "正式名称修改后，保留投稿写法作为别名"));

  const error = element("p", "organization-draft-error error");
  error.hidden = true;
  const reuseNotice = element("p", "organization-reuse-notice");
  reuseNotice.hidden = true;
  const footer = element("div", "organization-draft-footer");
  const confirm = element("button", "primary-button compact-button", "确认并处理下一个");
  confirm.type = "button";
  footer.append(aliasLabel, confirm);
  body.append(
    labeledControl("处理方式", action),
    existingPanel,
    createPanel,
    reuseNotice,
    sourceBar,
    error,
    footer,
  );
  details.append(summary, body);

  const initialExistingId =
    draft.suggestedIds.size === 1 ? [...draft.suggestedIds][0] : null;
  action.value = initialExistingId ? "existing" : "create";
  draft.restoreExistingId = initialExistingId;
  draft.confirmed = Boolean(initialExistingId);
  details.open = !draft.confirmed;

  const editor = {
    details,
    body,
    summary,
    status,
    action,
    existingPanel,
    existingInput,
    allowedExistingIds: new Set(),
    createPanel,
    organizationType,
    canonicalName,
    officialUrl,
    approvedDomains,
    reuseNotice,
    aliasLabel,
    saveAlias,
    error,
    confirm,
    restoreUrl,
  };
  draft.editor = editor;

  for (const control of [action, existingInput, organizationType]) {
    control.addEventListener("change", () => markOrganizationDraftChanged(draft, true));
  }
  saveAlias.addEventListener("change", () => markOrganizationDraftChanged(draft));
  canonicalName.addEventListener("input", () => markOrganizationDraftChanged(draft, true));
  officialUrl.addEventListener("input", () => markOrganizationDraftChanged(draft));
  approvedDomains.addEventListener("input", () => markOrganizationDraftChanged(draft));
  restoreUrl.addEventListener("click", () => {
    officialUrl.value = suggestedOfficialUrl(draft);
    markOrganizationDraftChanged(draft);
    officialUrl.focus();
  });
  confirm.addEventListener("click", () => void confirmOrganizationDraft(draft));
  return editor;
}

function renderOrganizationDraftTree(drafts) {
  const childrenByParent = new Map();
  for (const draft of drafts) {
    const children = childrenByParent.get(draft.parentKey) || [];
    children.push(draft);
    childrenByParent.set(draft.parentKey, children);
  }
  for (const children of childrenByParent.values()) {
    children.sort((first, second) => first.submittedName.localeCompare(second.submittedName, "zh-CN"));
  }
  const sortedChildren = (parentKey) => childrenByParent.get(parentKey) || [];
  const ordered = [];
  const renderBranch = (draft) => {
    ordered.push(draft);
    const branch = element("div", `organization-branch branch-${draft.level}`);
    branch.append(createOrganizationDraftEditor(draft).details);
    const children = sortedChildren(draft.key);
    if (children.length) {
      const container = element("div", "organization-children");
      for (const child of children) {
        container.append(renderBranch(child));
      }
      branch.append(container);
    }
    return branch;
  };
  for (const root of sortedChildren(null)) {
    nodes.organizationTree.append(renderBranch(root));
  }
  state.organizationDrafts = ordered;
}

async function resolveMentorProfileUrl(row) {
  const cached = state.profileUrlByProposalId.get(row.proposal_id);
  if (cached) {
    return cached;
  }

  if (Object.hasOwn(row, "profile_url")) {
    const directUrl = validateWebUrl(row.profile_url || row.source_url, "高校官网详情页");
    state.profileUrlByProposalId.set(row.proposal_id, directUrl);
    return directUrl;
  }

  const paddedRow = String(row.batch_row).padStart(4, "0");
  const proposalUrl =
    `https://raw.githubusercontent.com/${REPOSITORY}/${state.pullHeadSha}` +
    `/proposals/batch-issue-${state.issueNumber}/issue-${state.issueNumber}-row-${paddedRow}.json`;
  const response = await fetch(proposalUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`无法读取高校官网详情页（GitHub 返回 ${response.status}）`);
  }
  const proposalBuffer = await response.arrayBuffer();
  if (proposalBuffer.byteLength === 0 || proposalBuffer.byteLength > MAX_PROPOSAL_BYTES) {
    throw new Error("导师提案为空或超过页面处理上限");
  }
  const proposal = JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(proposalBuffer),
  );
  const submitted = proposal?.submitted;
  if (
    proposal?.id !== row.proposal_id ||
    proposal?.issue?.batch_row !== row.batch_row ||
    submitted?.name !== row.name ||
    submitted?.email !== row.email ||
    submitted?.source_url !== row.source_url
  ) {
    throw new Error("导师详情信息与审核清单中的导师不一致");
  }
  const profileUrl = validateWebUrl(submitted.profile_url || row.source_url, "高校官网详情页");
  state.profileUrlByProposalId.set(row.proposal_id, profileUrl);
  return profileUrl;
}

function createMentorProfileButton(row) {
  const button = element("button", "mentor-profile-button", "详情页 ↗");
  const defaultLabel = button.textContent;
  button.type = "button";
  button.setAttribute("aria-label", `在新标签页打开${row.name}的高校官网详情页`);
  button.title = "在新标签页打开高校官网详情页";
  button.addEventListener("click", async () => {
    const profileWindow = window.open("about:blank", "_blank");
    if (!profileWindow) {
      button.title = "浏览器拦截了新标签页，请允许此页面打开新窗口";
      return;
    }
    try {
      profileWindow.opener = null;
    } catch {
      profileWindow.close();
      button.title = "浏览器无法安全隔离新标签页";
      return;
    }
    button.disabled = true;
    button.textContent = "打开中…";
    try {
      const profileUrl = await resolveMentorProfileUrl(row);
      profileWindow.location.replace(profileUrl);
      button.title = "在新标签页打开高校官网详情页";
    } catch (error) {
      profileWindow.close();
      button.textContent = "打开失败";
      button.title = error instanceof Error ? error.message : "无法打开高校官网详情页";
      window.setTimeout(() => {
        button.textContent = defaultLabel;
      }, 2_000);
      return;
    } finally {
      button.disabled = false;
    }
    button.textContent = defaultLabel;
  });
  return button;
}

function restoreRowEditorValue(editor) {
  const value = state.restoredRowValues.get(editor.row.proposal_id);
  if (!value || typeof value !== "object") {
    return;
  }
  editor.action.value = storedText(value.action, 30) || "follow";
  editor.reason.value = storedText(value.reason, 500);
  editor.restoreTargetId = storedText(value.organization_id, 80) || null;
  if (
    editor.restoreTargetId &&
    editor.organizationInput.selectById(editor.restoreTargetId)
  ) {
    editor.restoreTargetId = null;
  }
  editor.organizationInput.hidden = editor.action.value !== "map_existing";
  editor.reason.hidden = editor.action.value !== "reject";
}

function createRowEditor(row) {
  const wrapper = element("div", "row-editor");
  const identity = element("div", "row-identity");
  const nameLine = element("div", "row-name-line");
  nameLine.append(element("strong", null, row.name), createMentorProfileButton(row));
  identity.append(nameLine, element("span", null, row.email));
  const action = createSelect(
    [
      ["follow", "跟随机构分组"],
      ["map_existing", "改到其他机构"],
      ["reject", "拒绝此导师"],
    ],
    "row-action",
    `${row.name}的逐行处理方式`,
  );
  const organizationInput = createOrganizationPicker(
    [...state.manifest.organizations, ...state.pendingOrganizations],
    "选择现有或本次新建的机构",
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
    scheduleReviewUpdate();
  });
  organizationInput.addEventListener("change", scheduleReviewUpdate);
  reason.addEventListener("input", scheduleReviewUpdate);
  wrapper.append(identity, action, organizationInput, reason);
  const editor = {
    row,
    wrapper,
    action,
    organizationInput,
    reason,
    restoreTargetId: null,
  };
  state.rowEditors.push(editor);
  state.rowEditorByProposalId.set(row.proposal_id, editor);
  restoreRowEditorValue(editor);
  return editor;
}

function updateGroupCard(card) {
  const rejecting = card.groupAction.value === "reject";
  card.groupReason.hidden = !rejecting;
  if (rejecting) {
    card.assignment.textContent = "整个分组将被拒绝；仍可在导师列表中单独改派个别导师。";
    card.article.dataset.kind = "rejected";
    return;
  }
  card.article.dataset.kind = "resolved";
  const drafts = LEVELS.map((level) =>
    state.organizationDraftByKey.get(card.group.draftKeys?.[level]),
  ).filter(Boolean);
  const target = [...drafts].reverse().find((draft) => !draft.forcedSkip && draft.targetId);
  if (!target) {
    card.assignment.textContent = "机构尚未确认";
    return;
  }
  const pending = drafts.filter((draft) => draftIsActive(draft) && !draft.confirmed);
  const lineage = target.lineageNames.join(" / ") || target.submittedName;
  card.assignment.textContent = pending.length ? `${lineage} · 还有 ${pending.length} 个机构待确认` : lineage;
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
  const assignment = element("p", "group-assignment", "机构尚未确认");

  const rowsDetails = element("details", "rows-details");
  const rowsSummary = element("summary", null, `拆分或拒绝个别导师（${group.rows.length} 位）`);
  const rowsContainer = element("div", "rows-container");
  const loadMoreRows = element("button", "text-button rows-load-more", "继续加载导师");
  loadMoreRows.type = "button";
  rowsDetails.append(rowsSummary, rowsContainer);
  article.append(header, sources, assignment, groupControls, rowsDetails);

  const card = {
    group,
    article,
    groupAction,
    groupReason,
    assignment,
    rowEditors: [],
    renderedRowCount: 0,
  };
  card.loadMoreRows = () => {
    const end = Math.min(card.renderedRowCount + 100, group.rows.length);
    const fragment = document.createDocumentFragment();
    for (const row of group.rows.slice(card.renderedRowCount, end)) {
      const editor = createRowEditor(row);
      card.rowEditors.push(editor);
      fragment.append(editor.wrapper);
    }
    card.renderedRowCount = end;
    rowsContainer.append(fragment);
    loadMoreRows.textContent = `继续加载（剩余 ${group.rows.length - end} 位）`;
    loadMoreRows.hidden = end >= group.rows.length;
    if (!loadMoreRows.hidden) {
      rowsContainer.append(loadMoreRows);
    }
  };
  rowsDetails.addEventListener("toggle", () => {
    if (rowsDetails.open && card.renderedRowCount === 0) {
      card.loadMoreRows();
    }
  });
  loadMoreRows.addEventListener("click", card.loadMoreRows);
  groupAction.addEventListener("change", scheduleReviewUpdate);
  groupReason.addEventListener("input", scheduleReviewUpdate);
  updateGroupCard(card);
  return card;
}

function draftIsActive(draft) {
  return draft.active !== false;
}

function descendantDrafts(draft) {
  return draft.descendants || [];
}

function markOrganizationDraftChanged(draft, affectsIdentity = false) {
  for (const affected of [draft, ...descendantDrafts(draft)]) {
    affected.confirmed = false;
    affected.editor.error.hidden = true;
  }
  if (affectsIdentity) {
    for (const descendant of descendantDrafts(draft)) {
      descendant.initialized = false;
      descendant.restoreExistingId = null;
      descendant.hasRestoredState = false;
    }
  }
  scheduleReviewUpdate();
}

function parseDomainsForPreview(value) {
  try {
    return parseDomains(value);
  } catch {
    return [];
  }
}

function removePendingOrganizationOptions() {
  for (const organizationId of state.pendingOrganizationIds) {
    const label = state.organizationLabelById.get(organizationId);
    state.selectableOrganizationById.delete(organizationId);
    state.organizationLabelById.delete(organizationId);
    if (label && state.organizationIdByLabel.get(label) === organizationId) {
      state.organizationIdByLabel.delete(label);
    }
  }
  state.pendingOrganizationIds.clear();
  state.pendingOrganizations = [];
}

function refreshPendingOrganizationOptions() {
  removePendingOrganizationOptions();
  const byId = new Map();
  for (const draft of state.organizationDrafts) {
    if (
      !draftIsActive(draft) ||
      draft.forcedSkip ||
      draft.editor.action.value !== "create" ||
      !draft.targetId
    ) {
      continue;
    }
    const parent = draft.parentKey ? state.organizationDraftByKey.get(draft.parentKey) : null;
    const organization = {
      id: draft.targetId,
      type: draft.editor.organizationType.value,
      canonical_name: draft.editor.canonicalName.value.trim(),
      parent_id: parent?.targetId || null,
      aliases: [],
      official_urls: draft.editor.officialUrl.value.trim()
        ? [draft.editor.officialUrl.value.trim()]
        : [],
      approved_domains: draft.effectiveDomains,
      lineage_ids: [...(parent?.lineageIds || []), draft.targetId],
      lineage_names: draft.lineageNames,
      pending: true,
    };
    const existing = byId.get(organization.id);
    if (existing) {
      existing.approved_domains = [
        ...new Set([...existing.approved_domains, ...organization.approved_domains]),
      ];
      continue;
    }
    byId.set(organization.id, organization);
  }
  state.pendingOrganizations = [...byId.values()].sort((first, second) =>
    organizationLabel(first).localeCompare(organizationLabel(second), "zh-CN"),
  );
  for (const organization of state.pendingOrganizations) {
    state.pendingOrganizationIds.add(organization.id);
    state.selectableOrganizationById.set(organization.id, organization);
    const label = organizationLabel(organization);
    state.organizationLabelById.set(organization.id, label);
    state.organizationIdByLabel.set(label, organization.id);
  }
  const options = [...state.manifest.organizations, ...state.pendingOrganizations];
  const signature = JSON.stringify(
    state.pendingOrganizations.map((organization) => [
      organization.id,
      organization.canonical_name,
      organization.approved_domains,
    ]),
  );
  if (signature !== state.pendingOrganizationsSignature) {
    state.pendingOrganizationsSignature = signature;
    for (const editor of state.rowEditors) {
      editor.organizationInput.setOptions(options);
      if (editor.restoreTargetId && editor.organizationInput.selectById(editor.restoreTargetId)) {
        editor.restoreTargetId = null;
      }
    }
  }
}

function draftValidationError(draft) {
  if (!draftIsActive(draft) || draft.forcedSkip) {
    return null;
  }
  const editor = draft.editor;
  const action = editor.action.value;
  if (action === "skip") {
    return draft.level === "university" ? "学校不能归到上级机构" : null;
  }
  const parent = draft.parentKey ? state.organizationDraftByKey.get(draft.parentKey) : null;
  if (draft.level !== "university" && !parent?.targetId) {
    return `请先确认上级${parent ? LEVEL_LABELS[parent.level] : "机构"}`;
  }
  if (action === "existing") {
    const organizationId = parseOrganizationInput(editor.existingInput, editor.allowedExistingIds);
    return organizationId ? null : `请选择一个现有${LEVEL_LABELS[draft.level]}`;
  }
  const canonicalName = editor.canonicalName.value.trim();
  if (!canonicalName) {
    return `${LEVEL_LABELS[draft.level]}「${draft.submittedName}」需要填写正式名称`;
  }
  const officialUrlValue = editor.officialUrl.value.trim();
  if (draft.level === "university" && !officialUrlValue) {
    return `学校「${canonicalName}」需要填写官方网站`;
  }
  let officialUrl = null;
  if (officialUrlValue) {
    try {
      officialUrl = validateWebUrl(officialUrlValue, `${LEVEL_LABELS[draft.level]}「${canonicalName}」的官网`);
    } catch (error) {
      return error instanceof Error ? error.message : "官方网站格式无效";
    }
  }
  let approvedDomains;
  try {
    approvedDomains = parseDomains(editor.approvedDomains.value);
  } catch (error) {
    return error instanceof Error ? error.message : "官方来源域名格式无效";
  }
  if (draft.level === "university" && approvedDomains.length === 0) {
    return `学校「${canonicalName}」至少需要一个官方来源域名`;
  }
  if (officialUrl) {
    const effectiveDomains = [...new Set([...(parent?.effectiveDomains || []), ...approvedDomains])];
    const officialHostname = new URL(officialUrl).hostname.toLowerCase().replace(/\.$/u, "");
    if (!effectiveDomains.some((domain) => hostMatchesDomain(officialHostname, domain))) {
      return `${LEVEL_LABELS[draft.level]}「${canonicalName}」的官网不属于本级或上级官方来源域名`;
    }
  }
  return null;
}

function showDraftValidation(draft) {
  const message = draftValidationError(draft);
  draft.editor.error.textContent = message || "";
  draft.editor.error.hidden = !message;
  draft.editor.details.classList.toggle("has-error", Boolean(message));
  return message;
}

async function updateOrganizationDrafts() {
  const token = ++state.updateToken;
  const rejectedGroupIds = new Set(
    state.cards
      .filter((card) => card.groupAction.value === "reject")
      .map((card) => card.group.id),
  );
  for (const draft of state.organizationDrafts) {
    draft.active = [...draft.groupIds].some((groupId) => !rejectedGroupIds.has(groupId));
  }
  for (const draft of state.organizationDrafts) {
    const editor = draft.editor;
    const parent = draft.parentKey ? state.organizationDraftByKey.get(draft.parentKey) : null;
    draft.forcedSkip = Boolean(parent && (parent.forcedSkip || parent.editor.action.value === "skip"));
    const parentId = parent?.targetId || null;
    const inheritedDomains = parent?.effectiveDomains || [];

    if (!draft.initialized && (draft.level === "university" || parentId)) {
      let initialExistingId = draft.restoreExistingId;
      if (!initialExistingId && !draft.hasRestoredState) {
        initialExistingId = findExactOrganization(draft.level, parentId, draft.submittedName);
        if (initialExistingId) {
          editor.action.value = "existing";
          draft.confirmed = true;
        }
      }
      draft.restoreExistingId = initialExistingId;
      draft.initialized = true;
    }

    const available = organizationsForLevel(draft.level, parentId);
    editor.allowedExistingIds = new Set(available.map((organization) => organization.id));
    editor.existingInput.setOptions(available);
    if (draft.restoreExistingId && editor.existingInput.selectById(draft.restoreExistingId)) {
      draft.restoreExistingId = null;
    }

    editor.reuseNotice.hidden = true;
    if (!draft.forcedSkip && editor.action.value === "create") {
      const exactExistingId = findExactOrganization(
        draft.level,
        parentId,
        editor.canonicalName.value,
      );
      if (
        exactExistingId &&
        editor.allowedExistingIds.has(exactExistingId) &&
        editor.existingInput.selectById(exactExistingId)
      ) {
        editor.action.value = "existing";
        const organization = state.organizationById.get(exactExistingId);
        editor.reuseNotice.textContent = `已找到同名机构，自动归到「${organization?.canonical_name || draft.submittedName}」。`;
        editor.reuseNotice.hidden = false;
      }
    }

    editor.action.disabled = draft.forcedSkip;
    const action = draft.forcedSkip ? "skip" : editor.action.value;
    editor.existingPanel.hidden = action !== "existing";
    editor.createPanel.hidden = action !== "create";
    editor.aliasLabel.hidden = action === "skip" || !draft.submittedName;
    editor.confirm.hidden = draft.forcedSkip || !draftIsActive(draft);

    draft.targetId = null;
    draft.effectiveDomains = inheritedDomains;
    draft.lineageNames = parent?.lineageNames || [];
    draft.lineageIds = parent?.lineageIds || [];
    if (draft.forcedSkip || action === "skip") {
      draft.targetId = parentId;
      draft.confirmed = draft.forcedSkip ? true : draft.confirmed;
    } else if (action === "existing") {
      const organizationId = parseOrganizationInput(editor.existingInput, editor.allowedExistingIds);
      const organization = state.organizationById.get(organizationId);
      if (organization) {
        draft.targetId = organization.id;
        draft.effectiveDomains = organization.approved_domains || [];
        draft.lineageNames = organization.lineage_names || [organization.canonical_name];
        draft.lineageIds = organization.lineage_ids || [organization.id];
      } else {
        draft.confirmed = false;
      }
    } else {
      const canonicalName = editor.canonicalName.value.trim();
      if (canonicalName && (draft.level === "university" || parentId)) {
        draft.targetId = await proposedOrganizationId(
          editor.organizationType.value,
          canonicalName,
          parentId,
        );
        if (token !== state.updateToken) {
          return;
        }
        draft.lineageNames = [...(parent?.lineageNames || []), canonicalName];
        draft.lineageIds = [...(parent?.lineageIds || []), draft.targetId];
      }
      draft.effectiveDomains = [
        ...new Set([...inheritedDomains, ...parseDomainsForPreview(editor.approvedDomains.value)]),
      ];
    }

    if (draft.confirmed && showDraftValidation(draft)) {
      draft.confirmed = false;
    }
    const active = draftIsActive(draft);
    const statusText = !active
      ? "未使用"
      : draft.forcedSkip
        ? "随上级"
        : draft.confirmed
          ? "已确认"
          : "待确认";
    editor.status.textContent = statusText;
    editor.details.dataset.status = statusText;
  }
  if (token !== state.updateToken) {
    return;
  }
  refreshPendingOrganizationOptions();
  for (const card of state.cards) {
    updateGroupCard(card);
  }
  updateOrganizationProgress();
  if (state.autosaveDirty) {
    state.autosaveDirty = false;
    scheduleAutosave();
  }
}

function pendingOrganizationDrafts() {
  return state.organizationDrafts.filter(
    (draft) => draftIsActive(draft) && !draft.forcedSkip && !draft.confirmed,
  );
}

function updateOrganizationProgress() {
  const active = state.organizationDrafts.filter(
    (draft) => draftIsActive(draft) && !draft.forcedSkip,
  );
  const pending = active.filter((draft) => !draft.confirmed);
  nodes.organizationProgress.textContent = pending.length
    ? `待确认 ${pending.length} / ${active.length}`
    : `已确认 ${active.length} 个机构`;
  nodes.nextPending.disabled = pending.length === 0;
  nodes.nextPending.textContent = pending.length ? `下一个待确认（${pending.length}）` : "机构已全部确认";
}

function focusNextPending(afterDraft = null) {
  const pending = pendingOrganizationDrafts();
  if (!pending.length) {
    return;
  }
  let target = pending[0];
  if (afterDraft) {
    const afterIndex = state.organizationDrafts.indexOf(afterDraft);
    target =
      pending.find((draft) => state.organizationDrafts.indexOf(draft) > afterIndex) || pending[0];
  }
  target.editor.details.open = true;
  target.editor.details.scrollIntoView({ behavior: "smooth", block: "center" });
  target.editor.summary.focus({ preventScroll: true });
}

async function confirmOrganizationDraft(draft) {
  await updateOrganizationDrafts();
  const message = showDraftValidation(draft);
  if (message) {
    draft.editor.details.open = true;
    return;
  }
  draft.confirmed = true;
  state.autosaveDirty = true;
  draft.editor.error.hidden = true;
  draft.editor.details.classList.remove("has-error");
  draft.editor.details.open = false;
  await updateOrganizationDrafts();
  focusNextPending(draft);
}

let reviewUpdateTimer = null;
let autosaveTimer = null;

function scheduleReviewUpdate() {
  state.autosaveDirty = true;
  nodes.decisionOutput.hidden = true;
  nodes.decisionPreview.hidden = true;
  window.clearTimeout(reviewUpdateTimer);
  reviewUpdateTimer = window.setTimeout(() => {
    reviewUpdateTimer = null;
    void updateOrganizationDrafts();
  }, 80);
}

function serializeReviewDraft() {
  const rows = Object.fromEntries(state.restoredRowValues);
  for (const editor of state.rowEditors) {
    const value = {
      action: editor.action.value,
      organization_id: parseOrganizationInput(editor.organizationInput),
      reason: editor.reason.value,
    };
    if (value.action === "follow" && !value.organization_id && !value.reason) {
      delete rows[editor.row.proposal_id];
    } else {
      rows[editor.row.proposal_id] = value;
    }
  }
  return {
    version: 1,
    saved_at: new Date().toISOString(),
    organizations: Object.fromEntries(
      state.organizationDrafts.map((draft) => [
        draft.key,
        {
          action: draft.editor.action.value,
          existing_id: parseOrganizationInput(draft.editor.existingInput),
          organization_type: draft.editor.organizationType.value,
          canonical_name: draft.editor.canonicalName.value,
          official_url: draft.editor.officialUrl.value,
          approved_domains: draft.editor.approvedDomains.value,
          save_alias: draft.editor.saveAlias.checked,
          confirmed: draft.confirmed,
        },
      ]),
    ),
    groups: Object.fromEntries(
      state.cards.map((card) => [
        card.group.id,
        { action: card.groupAction.value, reason: card.groupReason.value },
      ]),
    ),
    rows,
  };
}

function saveReviewDraft() {
  if (!state.storageKey) {
    return;
  }
  try {
    localStorage.setItem(state.storageKey, JSON.stringify(serializeReviewDraft()));
    nodes.autosaveStatus.textContent = `审核进度已自动保存 · ${new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    })}`;
  } catch {
    nodes.autosaveStatus.textContent = "浏览器未允许自动保存，请在完成前不要关闭页面。";
  }
}

function scheduleAutosave() {
  if (!state.storageKey) {
    return;
  }
  window.clearTimeout(autosaveTimer);
  autosaveTimer = window.setTimeout(saveReviewDraft, 800);
}

function storedText(value, maximumLength) {
  return typeof value === "string" ? value.slice(0, maximumLength) : "";
}

function restoreReviewDraft() {
  if (!state.storageKey) {
    return false;
  }
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(state.storageKey) || "null");
  } catch {
    return false;
  }
  if (!saved || saved.version !== 1) {
    return false;
  }
  for (const draft of state.organizationDrafts) {
    const value = saved.organizations?.[draft.key];
    if (!value || typeof value !== "object") {
      continue;
    }
    draft.editor.action.value = storedText(value.action, 20);
    draft.editor.organizationType.value = storedText(value.organization_type, 30);
    draft.editor.canonicalName.value = storedText(value.canonical_name, 255);
    draft.editor.officialUrl.value = storedText(value.official_url, 500);
    draft.editor.approvedDomains.value = storedText(value.approved_domains, 2000);
    draft.editor.saveAlias.checked = Boolean(value.save_alias);
    draft.confirmed = Boolean(value.confirmed);
    draft.restoreExistingId = storedText(value.existing_id, 80) || null;
    draft.hasRestoredState = true;
    draft.initialized = false;
  }
  for (const card of state.cards) {
    const value = saved.groups?.[card.group.id];
    if (!value || typeof value !== "object") {
      continue;
    }
    card.groupAction.value = storedText(value.action, 20);
    card.groupReason.value = storedText(value.reason, 500);
  }
  state.restoredRowValues.clear();
  for (const group of state.manifest.groups) {
    for (const row of group.rows) {
      const value = saved.rows?.[row.proposal_id];
      if (!value || typeof value !== "object") {
        continue;
      }
      const restored = {
        action: storedText(value.action, 30) || "follow",
        organization_id: storedText(value.organization_id, 80) || null,
        reason: storedText(value.reason, 500),
      };
      if (restored.action !== "follow" || restored.organization_id || restored.reason) {
        state.restoredRowValues.set(row.proposal_id, restored);
      }
    }
  }
  for (const editor of state.rowEditors) {
    restoreRowEditorValue(editor);
  }
  nodes.autosaveStatus.textContent = "已恢复这个 PR 上次自动保存的审核进度。";
  return true;
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
    throw new Error(`官方来源域名格式无效：${invalid}`);
  }
  return domains;
}

function hostMatchesDomain(hostname, domain) {
  return hostname === domain || hostname.endsWith(`.${domain}`);
}

function sourceUrlMatchesDomains(sourceUrl, domains) {
  const hostname = new URL(sourceUrl).hostname.toLowerCase().replace(/\.$/u, "");
  return domains.some((domain) => hostMatchesDomain(hostname, domain));
}

async function collectLevels(group) {
  const levels = [];
  let parentId = null;
  let inheritedDomains = [];
  let skippedSchool = false;
  let targetId = null;
  for (const level of LEVELS) {
    const draft = state.organizationDraftByKey.get(group.draftKeys?.[level]);
    if (!draft) {
      if (level === "university") {
        throw new Error(`分组“${pathText(group)}”没有学校名称`);
      }
      if (level === "school") {
        skippedSchool = true;
      }
      levels.push({
        level,
        action: "skip",
        organization_id: null,
        organization_type: null,
        canonical_name: null,
        official_url: null,
        approved_domains: [],
        save_submitted_as_alias: false,
      });
      continue;
    }
    const editor = draft.editor;
    const action = draft.forcedSkip ? "skip" : editor.action.value;
    if (!draft.confirmed && !draft.forcedSkip) {
      draft.editor.details.open = true;
      draft.editor.details.scrollIntoView({ behavior: "smooth", block: "center" });
      throw new Error(`${LEVEL_LABELS[level]}「${draft.submittedName}」尚未确认`);
    }
    const validationMessage = showDraftValidation(draft);
    if (validationMessage) {
      draft.confirmed = false;
      draft.editor.details.open = true;
      throw new Error(validationMessage);
    }
    if (action === "skip") {
      if (level === "university") {
        throw new Error(`学校「${draft.submittedName}」不能归到上级机构`);
      }
      if (level === "school") {
        skippedSchool = true;
      }
      levels.push({
        level,
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
      throw new Error(`系所「${draft.submittedName}」不能在归到学校后单独建立`);
    }
    if (action === "existing") {
      const organizationId = parseOrganizationInput(
        editor.existingInput,
        editor.allowedExistingIds,
      );
      if (!organizationId) {
        throw new Error(`${LEVEL_LABELS[level]}「${draft.submittedName}」需要选择现有机构`);
      }
      levels.push({
        level,
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
      targetId = organizationId;
      continue;
    }
    const canonicalName = editor.canonicalName.value.trim();
    if (!canonicalName) {
      throw new Error(`${LEVEL_LABELS[level]}「${draft.submittedName}」需要填写正式名称`);
    }
    const officialUrlValue = editor.officialUrl.value.trim();
    const officialUrl = officialUrlValue
      ? validateWebUrl(officialUrlValue, `${LEVEL_LABELS[level]}「${canonicalName}」的官网`)
      : null;
    if (level === "university" && !officialUrl) {
      throw new Error(`学校「${canonicalName}」需要填写官方网站`);
    }
    const approvedDomains = parseDomains(editor.approvedDomains.value);
    if (level === "university" && approvedDomains.length === 0) {
      throw new Error(`学校「${canonicalName}」至少需要一个官方来源域名`);
    }
    const effectiveDomains = [...new Set([...inheritedDomains, ...approvedDomains])];
    if (officialUrl && !sourceUrlMatchesDomains(officialUrl, effectiveDomains)) {
      throw new Error(`${LEVEL_LABELS[level]}「${canonicalName}」的官网不属于官方来源域名`);
    }
    const organizationType = editor.organizationType.value;
    levels.push({
      level,
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
    targetId = parentId;
  }
  for (const sourceUrl of group.source_urls) {
    if (!sourceUrlMatchesDomains(sourceUrl, inheritedDomains)) {
      throw new Error(`分组“${pathText(group)}”的来源域名尚未被所属机构批准`);
    }
  }
  return { levels, targetId, effectiveDomains: inheritedDomains };
}

function collectRowOverrides(card) {
  const overrides = [];
  for (const row of card.group.rows) {
    const editor = state.rowEditorByProposalId.get(row.proposal_id);
    const restored = state.restoredRowValues.get(row.proposal_id);
    const action = editor?.action.value || restored?.action || "follow";
    if (action === "follow") {
      continue;
    }
    if (action === "map_existing") {
      const organizationId = editor
        ? parseOrganizationInput(editor.organizationInput)
        : restored?.organization_id;
      if (!organizationId) {
        throw new Error(`${row.name}需要选择现有或本次新建的机构`);
      }
      const organization = state.selectableOrganizationById.get(organizationId);
      const verificationUrls = [row.profile_url, row.source_url].filter(Boolean);
      if (
        !organization ||
        !verificationUrls.every((url) =>
          sourceUrlMatchesDomains(url, organization.approved_domains || []),
        )
      ) {
        throw new Error(`${row.name}的详情页或发现来源页不属于改派机构的官方来源域名`);
      }
      overrides.push({
        proposal_id: row.proposal_id,
        action: "map_existing",
        organization_id: organizationId,
        reason: null,
      });
      continue;
    }
    if (action !== "reject") {
      throw new Error(`${row.name}的逐行处理方式无效`);
    }
    const reason = (editor?.reason.value || restored?.reason || "").trim();
    if (!reason) {
      throw new Error(`拒绝${row.name}时需要填写原因`);
    }
    overrides.push({
      proposal_id: row.proposal_id,
      action: "reject",
      organization_id: null,
      reason,
    });
  }
  return overrides;
}

async function collectDecision() {
  await updateOrganizationDrafts();
  const pending = pendingOrganizationDrafts();
  if (pending.length) {
    focusNextPending();
    throw new Error(`还有 ${pending.length} 个机构尚未确认`);
  }
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
      levels: (await collectLevels(card.group)).levels,
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

function renderDecisionPreview(decision) {
  const createdIds = new Set(
    state.organizationDrafts
      .filter(
        (draft) =>
          draftIsActive(draft) &&
          !draft.forcedSkip &&
          draft.editor.action.value === "create" &&
          draft.targetId,
      )
      .map((draft) => draft.targetId),
  );
  let mappedRows = 0;
  let rejectedRows = 0;
  let adjustedRows = 0;
  const decisionByGroup = new Map(decision.decisions.map((item) => [item.group_id, item]));
  for (const card of state.cards) {
    const groupDecision = decisionByGroup.get(card.group.id);
    const overrides = new Map(
      groupDecision.row_overrides.map((item) => [item.proposal_id, item]),
    );
    for (const row of card.group.rows) {
      const override = overrides.get(row.proposal_id);
      if (override?.action === "reject" || (!override && groupDecision.action === "reject")) {
        rejectedRows += 1;
      } else {
        mappedRows += 1;
        if (override?.action === "map_existing") {
          adjustedRows += 1;
        }
      }
    }
  }
  const metrics = [
    ["新建机构", createdIds.size],
    ["导入导师", mappedRows],
    ["改派导师", adjustedRows],
    ["拒绝导师", rejectedRows],
  ];
  nodes.decisionPreview.replaceChildren();
  for (const [label, value] of metrics) {
    const item = element("div");
    item.append(element("span", null, label), element("strong", null, String(value)));
    nodes.decisionPreview.append(item);
  }
  nodes.decisionPreview.hidden = false;
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
    renderDecisionPreview(decision);
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
    state.pullHeadSha = pull.head.sha;
    state.issueNumber = issueNumber;
    state.manifest = manifest;
    state.manifestSha256 = await sha256Hex(manifestBuffer);
    state.storageKey = `mentor-data-review:${pullNumber}:${state.manifestSha256}`;
    for (const organization of manifest.organizations) {
      state.organizationById.set(organization.id, organization);
      state.selectableOrganizationById.set(organization.id, organization);
      const level = organizationLevel(organization.type);
      const parentKey = levelParentKey(level, organization.parent_id || null);
      const organizations = state.organizationsByLevelParent.get(parentKey) || [];
      organizations.push(organization);
      state.organizationsByLevelParent.set(parentKey, organizations);
      for (const name of [organization.canonical_name, ...(organization.aliases || [])]) {
        const normalizedName = normalizeOrganizationName(name);
        if (!normalizedName) {
          continue;
        }
        const exactKey = `${parentKey}\u001f${normalizedName}`;
        const ids = state.organizationIdsByExactName.get(exactKey) || [];
        if (!ids.includes(organization.id)) {
          ids.push(organization.id);
        }
        state.organizationIdsByExactName.set(exactKey, ids);
      }
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
      const drafts = buildOrganizationDrafts();
      renderOrganizationDraftTree(drafts);
      nodes.organizationsSection.hidden = false;
      nodes.groupsSection.hidden = false;
      for (const [index, group] of manifest.groups.entries()) {
        const card = createGroupCard(group, index);
        state.cards.push(card);
        state.groupCardById.set(group.id, card);
        nodes.groups.append(card.article);
      }
      const restored = restoreReviewDraft();
      await updateOrganizationDrafts();
      const pending = pendingOrganizationDrafts();
      for (const [index, draft] of pending.entries()) {
        draft.editor.details.open = restored ? draft.editor.details.open : index === 0;
      }
    } else {
      nodes.emptyReview.hidden = false;
    }
    nodes.decisionPanel.hidden = false;
    setStatus(
      "ready",
      "审核清单已验证",
      `同一机构只需确认一次；提交结果前仍会由后端复核 PR #${pullNumber}。`,
    );
  } catch (error) {
    setStatus("error", "无法打开审核清单", error instanceof Error ? error.message : "未知错误");
  }
}

nodes.generate.addEventListener("click", () => void generateDecision());
nodes.copyOpen.addEventListener("click", () => void copyAndOpenPullRequest());
nodes.nextPending.addEventListener("click", () => focusNextPending());
window.addEventListener("pagehide", saveReviewDraft);
void loadReview();
