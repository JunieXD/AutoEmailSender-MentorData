const REPOSITORY = "JunieXD/AutoEmailSender-MentorData";
const COMMENT_MARKER = "<!-- mentor-data-organization-review:v1 -->";
const BRANCH_PATTERN = /^batch\/issue-([1-9][0-9]*)$/;
const SHA_PATTERN = /^[a-f0-9]{40,64}$/;
const DOMAIN_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;
const MAX_MANIFEST_BYTES = 20_000_000;
const GITHUB_COMMENT_CHARACTER_LIMIT = 65_536;
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
const ALL_ORGANIZATION_TYPES = Object.keys(TYPE_LABELS);
const SCHOOL_ORGANIZATION_TYPES = new Set(["school", "institute"]);
const DEPARTMENT_ORGANIZATION_TYPES = new Set(["department", "center", "laboratory"]);
const IDENTITY_CONFLICT_REJECTION_REASON = "同邮箱导师已存在，保留社区库现有记录";
const ORGANIZATION_TYPE_SUFFIX_RULES = {
  school: [
    ["研究院", "institute"],
    ["研究所", "institute"],
    ["学院", "school"],
  ],
  department: [
    ["实验室", "laboratory"],
    ["研究室", "laboratory"],
    ["中心", "center"],
    ["办公室", "department"],
    ["系", "department"],
    ["部", "department"],
    ["研究所", "department"],
  ],
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
  workflowTasks: [],
  workflowTaskById: new Map(),
  workflowNodes: [],
  workflowRootNodes: [],
  workflowNodeById: new Map(),
  workflowNodeIdByTaskId: new Map(),
  workflowFilter: "pending",
  currentWorkflowTaskId: null,
  currentWorkflowNodeId: null,
  expandedWorkflowNodeIds: new Set(),
  workflowSearchQuery: "",
  lastCompletionAt: 0,
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
  identityCount: document.querySelector("#review-identity-count"),
  invalidSummary: document.querySelector("#review-invalid-summary"),
  identitySummary: document.querySelector("#review-identity-summary"),
  invalidPanel: document.querySelector("#invalid-rows-panel"),
  invalidRows: document.querySelector("#invalid-rows"),
  reviewWorkspace: document.querySelector("#review-workspace"),
  workflowPendingCount: document.querySelector("#workflow-pending-count"),
  taskFilterPending: document.querySelector("#task-filter-pending"),
  taskFilterPendingCount: document.querySelector("#task-filter-pending-count"),
  taskFilterDone: document.querySelector("#task-filter-done"),
  taskFilterDoneCount: document.querySelector("#task-filter-done-count"),
  taskFilterAll: document.querySelector("#task-filter-all"),
  taskFilterAllCount: document.querySelector("#task-filter-all-count"),
  treeSearch: document.querySelector("#review-tree-search"),
  organizationTree: document.querySelector("#review-organization-tree"),
  taskStage: document.querySelector("#review-node-stage"),
  taskEmpty: document.querySelector("#review-node-empty"),
  nodeContent: document.querySelector("#review-node-content"),
  autosaveStatus: document.querySelector("#autosave-status"),
  emptyReview: document.querySelector("#empty-review"),
  decisionPanel: document.querySelector("#decision-panel"),
  generate: document.querySelector("#generate-decision"),
  decisionError: document.querySelector("#decision-error"),
  decisionPreview: document.querySelector("#decision-preview"),
  decisionOutput: document.querySelector("#decision-output"),
  decisionText: document.querySelector("#decision-text"),
  copyOpen: document.querySelector("#copy-open-pr"),
  copyStatus: document.querySelector("#copy-status"),
  previousTask: document.querySelector("#previous-review-task"),
  confirmTask: document.querySelector("#confirm-review-task"),
  nextTask: document.querySelector("#next-review-task"),
  workflowPosition: document.querySelector("#workflow-position"),
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

class ReviewValidationError extends Error {
  constructor(message, { card = null, draft = null, row = null, focusTarget = null } = {}) {
    super(message);
    this.name = "ReviewValidationError";
    this.taskId = card
      ? `group:${card.group.id}`
      : draft
        ? `organization:${draft.key}`
        : null;
    this.card = card;
    this.draft = draft;
    this.proposalId = row?.proposal_id || null;
    this.focusTarget = focusTarget;
  }
}

function nextCompletionTime() {
  state.lastCompletionAt = Math.max(Date.now(), state.lastCompletionAt + 1);
  return state.lastCompletionAt;
}

function markWorkflowCompleted(item) {
  item.completedAt = nextCompletionTime();
}

function clearWorkflowCompletion(item) {
  item.completedAt = null;
}

function setStatus(kind, title, detail) {
  nodes.status.dataset.kind = kind;
  nodes.status.hidden = kind === "ready";
  nodes.statusTitle.textContent = title;
  nodes.statusDetail.textContent = detail;
}

function normalizeOrganizationName(value) {
  return MentorReviewLogic.normalizeOrganizationName(value);
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

function inferOrganizationType(level, canonicalName) {
  const normalizedName = String(canonicalName || "").normalize("NFKC").trim();
  const fallback = LEVEL_TYPES[level]?.[0] || "department";
  for (const [suffix, organizationType] of ORGANIZATION_TYPE_SUFFIX_RULES[level] || []) {
    if (normalizedName.endsWith(suffix)) {
      return organizationType;
    }
  }
  return fallback;
}

function shouldDefaultOrganizationToParent(level, canonicalName, parentCanonicalName) {
  if (level !== "department") {
    return false;
  }
  return Boolean(
    MentorReviewLogic.parentOrganizationNameMatch({
      school: parentCanonicalName,
      department: canonicalName,
    }),
  );
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

function identitySchoolEvidenceForDraft(draft) {
  if (draft.level !== "school") {
    return null;
  }
  const rows = state.manifest.groups
    .filter((group) => draft.groupIds.has(group.id))
    .flatMap((group) => group.rows);
  return MentorReviewLogic.identitySchoolEvidence(rows, state.manifest.organizations);
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
function handleFloatingControlScroll(event) {
  const current = activeFloatingControl;
  if (!current) {
    return;
  }
  if (event.target instanceof Node && current.popup.contains(event.target)) {
    return;
  }
  closeActiveFloatingControl();
}

window.addEventListener("resize", closeActiveFloatingControl);
window.addEventListener("scroll", handleFloatingControlScroll, true);

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
    menu.remove();
  }

  function openMenu() {
    if (trigger.disabled || open) {
      return;
    }
    if (!menu.isConnected) {
      document.body.append(menu);
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
    const query = input.value.trim();
    const selectedLabel = selectedId ? state.organizationLabelById.get(selectedId) : null;
    const searching = Boolean(query && query !== selectedLabel);
    visibleOrganizations = (
      searching
        ? MentorReviewLogic.rankOrganizationSearchResults(availableOrganizations, query)
        : availableOrganizations
    ).slice(0, 80);
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
    menu.remove();
  }

  function openMenu() {
    if (open) {
      return;
    }
    if (!menu.isConnected) {
      document.body.append(menu);
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
  root.focus = () => input.focus();

  root.append(input, toggle);
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

function taskContext(group) {
  return (
    String(group.submitted.department || "").trim() ||
    String(group.submitted.school || "").trim() ||
    String(group.submitted.university || "").trim() ||
    "未填写机构"
  );
}

function draftPath(draft) {
  const path = [];
  let current = draft;
  while (current) {
    path.unshift({ level: current.level, name: current.submittedName });
    current = current.parentKey ? state.organizationDraftByKey.get(current.parentKey) : null;
  }
  return path;
}

function draftParentContext(draft) {
  return draftPath(draft)
    .slice(0, -1)
    .map(({ name }) => name)
    .filter(Boolean)
    .join(" / ");
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
          completedAt: null,
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
          autoMatchedExistingId: null,
          autoMatchedExistingReason: null,
          editor: null,
        };
        draftByKey.set(key, draft);
      }
      draft.groupIds.add(group.id);
      draft.rowCount += group.rows.length;
      for (const row of group.rows) {
        const sourceUrl = row.source_url;
        draft.sourceUrlCounts.set(
          sourceUrl,
          (draft.sourceUrlCounts.get(sourceUrl) || 0) + 1,
        );
        draft.sourceDomains.add(new URL(sourceUrl).hostname.toLocaleLowerCase().replace(/\.$/u, ""));
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
  const summaryNames = element("span", "organization-summary-names");
  const parentContext = draftParentContext(draft);
  summaryNames.append(element("strong", null, draft.submittedName));
  if (parentContext) {
    summaryNames.append(element("small", null, parentContext));
  }
  summaryIdentity.append(
    element("span", "organization-level-label", LEVEL_LABELS[draft.level]),
    summaryNames,
  );
  const summaryMeta = element("span", "organization-summary-meta");
  const impact = element(
    "span",
    "organization-impact",
    `${draft.groupIds.size} 组 · ${draft.rowCount} 位导师`,
  );
  const status = element("span", "organization-draft-status", "待处理");
  summaryMeta.append(impact, status);
  summary.append(summaryIdentity, summaryMeta);

  const body = element("div", "organization-draft-body");
  const actionOptions = [
    ["existing", "归入已有机构"],
    ["create", "新建此机构"],
  ];
  if (draft.level !== "university") {
    actionOptions.push(["skip", "归入上级"]);
  }
  const action = createSelect(
    actionOptions,
    "level-action",
    `${LEVEL_LABELS[draft.level]}处理方式`,
  );

  const existingPanel = element("div", "editor-panel existing-panel");
  const existingInput = createOrganizationPicker(
    [],
    "输入学校、学院或系所",
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
  organizationType.value = inferOrganizationType(draft.level, canonicalName.value);
  const officialUrl = createInput(
    "url",
    draft.level === "university" ? "http:// 或 https:// 官网" : "官网（可留空）",
    "official-url",
  );
  officialUrl.setAttribute("aria-label", `${LEVEL_LABELS[draft.level]}官方网站`);
  officialUrl.maxLength = 500;
  officialUrl.value = draft.level === "university" ? suggestedOfficialUrl(draft) : "";
  const approvedDomains = createInput(
    "text",
    "域名，多个用逗号分隔",
    "approved-domains",
  );
  approvedDomains.setAttribute("aria-label", `${LEVEL_LABELS[draft.level]}官方来源域名`);
  approvedDomains.maxLength = 2000;
  if (draft.level === "university") {
    approvedDomains.value = [...draft.sourceDomains].sort().join(", ");
  }
  if (parentContext) {
    const createContext = element("p", "organization-create-context");
    createContext.append(
      element("span", null, "新建位置"),
      element("strong", null, parentContext),
    );
    createPanel.append(createContext);
  }
  createPanel.append(
    labeledControl("机构类型", organizationType),
    labeledControl("正式名称", canonicalName),
    labeledControl(
      "官网",
      officialUrl,
      officialUrl.value ? "已从投稿来源填写。" : "",
    ),
    labeledControl(
      draft.level === "university" ? "官方域名" : "额外域名",
      approvedDomains,
      "",
    ),
  );

  const sourceBar = element("div", "organization-source-bar");
  const sourceGroup = {
    source_urls: [...draft.sourceUrlCounts.keys()].sort(),
  };
  sourceBar.append(element("span", null, "来源"), sourceLinks(sourceGroup));
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
  aliasLabel.append(saveAlias, element("span", null, "投稿名称作为别名"));

  const error = element("p", "organization-draft-error error");
  error.hidden = true;
  const reuseNotice = element("p", "organization-reuse-notice");
  reuseNotice.hidden = true;
  const defaultNotice = element("p", "organization-reuse-notice organization-default-notice");
  defaultNotice.hidden = true;
  const footer = element("div", "organization-draft-footer");
  footer.append(aliasLabel);
  body.append(
    labeledControl("处理方式", action),
    existingPanel,
    createPanel,
    reuseNotice,
    defaultNotice,
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
    defaultNotice,
    aliasLabel,
    saveAlias,
    error,
    restoreUrl,
    actionManuallySelected: false,
    organizationTypeManuallySelected: false,
    autoSkippedToParent: false,
    suggestedSkipToParent: false,
  };
  draft.editor = editor;

  action.addEventListener("change", () => {
    editor.actionManuallySelected = true;
    editor.autoSkippedToParent = false;
    editor.suggestedSkipToParent = false;
    draft.autoMatchedExistingId = null;
    draft.autoMatchedExistingReason = null;
    editor.defaultNotice.hidden = true;
    markOrganizationDraftChanged(draft, true);
  });
  existingInput.addEventListener("change", () => {
    draft.autoMatchedExistingId = null;
    draft.autoMatchedExistingReason = null;
    markOrganizationDraftChanged(draft, true);
  });
  organizationType.addEventListener("change", () => {
    editor.organizationTypeManuallySelected = true;
    markOrganizationDraftChanged(draft, true);
  });
  saveAlias.addEventListener("change", () => markOrganizationDraftChanged(draft));
  canonicalName.addEventListener("input", () => {
    if (!editor.organizationTypeManuallySelected) {
      organizationType.value = inferOrganizationType(draft.level, canonicalName.value);
    }
    markOrganizationDraftChanged(draft, true);
  });
  officialUrl.addEventListener("input", () => markOrganizationDraftChanged(draft));
  approvedDomains.addEventListener("input", () => markOrganizationDraftChanged(draft));
  restoreUrl.addEventListener("click", () => {
    officialUrl.value = suggestedOfficialUrl(draft);
    markOrganizationDraftChanged(draft);
    officialUrl.focus();
  });
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
  const prepareBranch = (draft) => {
    ordered.push(draft);
    createOrganizationDraftEditor(draft);
    for (const child of sortedChildren(draft.key)) {
      prepareBranch(child);
    }
  };
  for (const root of sortedChildren(null)) {
    prepareBranch(root);
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
    throw new Error("无法读取这位导师的数据：文件为空或过大");
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

function organizationPathForId(organizationId) {
  const organization =
    state.selectableOrganizationById.get(organizationId) || state.organizationById.get(organizationId);
  return organization?.lineage_names?.join(" / ") || organizationId;
}

function standardGroupTargetId(group) {
  const drafts = LEVELS.map((level) =>
    state.organizationDraftByKey.get(group.draftKeys?.[level]),
  ).filter(Boolean);
  return [...drafts].reverse().find((draft) => !draft.forcedSkip && draft.targetId)?.targetId || null;
}

function groupTargetId(card) {
  if (card.mappingMode.value === "corrected") {
    return card.independentTargetId || null;
  }
  return standardGroupTargetId(card.group);
}

function effectiveRowTargetId(card, editor) {
  if (editor.action.value === "reject") {
    return null;
  }
  if (editor.action.value === "map_existing") {
    return parseOrganizationInput(editor.organizationInput);
  }
  if (card.groupAction.value === "reject") {
    return null;
  }
  return groupTargetId(card);
}

function isCurrentIdentityOrganization(editor, organizationId) {
  return Boolean(
    editor &&
      organizationId &&
      editor.row.identity?.mentor?.affiliations?.some(
        (affiliation) => affiliation.organization_id === organizationId,
      ),
  );
}

function identityAffiliationLabel(affiliation) {
  const primary = affiliation.is_primary ? " · 主要任职" : " · 兼任";
  const title = affiliation.title ? ` · ${affiliation.title}` : "";
  const organization = affiliation.organization_id
    ? organizationPathForId(affiliation.organization_id)
    : affiliation.organization_label || "本批次另一条投稿（机构待审核）";
  return `${organization}${primary}${title}`;
}

function identityComparisonRow(label, incoming, existing, different = false) {
  const row = document.createElement("tr");
  const heading = document.createElement("th");
  heading.scope = "row";
  heading.append(element("span", null, label));
  if (different) {
    heading.append(element("span", "identity-diff-marker", "不同"));
    row.dataset.different = "true";
  }
  const incomingCell = document.createElement("td");
  const existingCell = document.createElement("td");
  incomingCell.append(incoming instanceof Node ? incoming : document.createTextNode(incoming));
  existingCell.append(existing instanceof Node ? existing : document.createTextNode(existing));
  row.append(heading, incomingCell, existingCell);
  return row;
}

function identityAffiliationsList(affiliations) {
  const list = element("ul", "identity-affiliations");
  for (const affiliation of affiliations) {
    const item = element("li");
    const source = element("a", "identity-affiliation-source", "证据 ↗");
    source.href = affiliation.source_url;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    item.append(element("span", null, identityAffiliationLabel(affiliation)), source);
    list.append(item);
  }
  return list;
}

function identityValueMatches(incoming, candidates) {
  const normalizedIncoming = MentorReviewLogic.compactOrganizationName(incoming);
  return Boolean(
    normalizedIncoming &&
      candidates.some(
        (candidate) => MentorReviewLogic.compactOrganizationName(candidate) === normalizedIncoming,
      ),
  );
}

function updateIdentityResolutionState(editor) {
  if (!editor.identityPanel) {
    return;
  }
  const targetId = effectiveRowTargetId(editor.card, editor);
  const rejected =
    editor.action.value === "reject" ||
    (editor.action.value !== "map_existing" && editor.card.groupAction.value === "reject");
  const alreadyCurrent = isCurrentIdentityOrganization(editor, targetId);
  const action = editor.identityAction.value;
  editor.identityTargetId = targetId;
  editor.identityActionField.hidden = rejected || alreadyCurrent;
  editor.identityReasonField.hidden = rejected || alreadyCurrent;
  editor.identityControls.hidden = rejected || alreadyCurrent;
  editor.identityPrimaryOption.hidden =
    rejected || alreadyCurrent || action !== "append_current_affiliation";
  editor.identityFormerField.hidden =
    rejected || alreadyCurrent || action !== "transfer_current_affiliation";

  if (rejected) {
    editor.identityStatus.textContent = "默认不收录本次提交；现有记录不变。";
    editor.identityPanel.dataset.state = "rejected";
  } else if (!targetId) {
    editor.identityStatus.textContent = "先确认归属。";
    editor.identityPanel.dataset.state = "pending";
  } else if (alreadyCurrent) {
    editor.identityStatus.textContent = "已是现任职，无需新增。";
    editor.identityPanel.dataset.state = "matched";
  } else {
    editor.identityStatus.textContent = `归入「${organizationPathForId(targetId)}」：请选择双聘或调动。`;
    editor.identityPanel.dataset.state = "required";
  }
}

function createIdentityResolutionPanel(row, card) {
  const panel = element("section", "identity-resolution");
  panel.setAttribute("aria-label", `${row.name}的导师身份与任职判定`);
  const heading = element("div", "identity-resolution-heading");
  const headingCopy = element("div");
  headingCopy.append(element("strong", null, "同邮箱记录对比"));
  const status = element("p", "identity-resolution-status");
  heading.append(headingCopy, status);

  const comparison = element("div", "identity-comparison");
  const table = element("table", "identity-comparison-table");
  const tableHead = document.createElement("thead");
  const headingRow = document.createElement("tr");
  for (const label of ["字段", "本次投稿", "社区库现有记录"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    headingRow.append(cell);
  }
  tableHead.append(headingRow);
  const tableBody = document.createElement("tbody");
  const affiliations = row.identity.mentor.affiliations;
  const existingPaths = affiliations.map((affiliation) =>
    affiliation.organization_id
      ? organizationPathForId(affiliation.organization_id)
      : affiliation.organization_label || "机构待审核",
  );
  const existingTitles = affiliations.map((affiliation) => affiliation.title).filter(Boolean);
  tableBody.append(
    identityComparisonRow(
      "姓名",
      row.name,
      row.identity.mentor.name,
      !identityValueMatches(row.name, [row.identity.mentor.name]),
    ),
    identityComparisonRow(
      "邮箱",
      row.email,
      row.identity.mentor.email,
      row.email.trim().toLocaleLowerCase() !== row.identity.mentor.email.trim().toLocaleLowerCase(),
    ),
    identityComparisonRow(
      "机构",
      pathText(card.group),
      identityAffiliationsList(affiliations),
      !identityValueMatches(pathText(card.group), existingPaths),
    ),
    identityComparisonRow(
      "职称",
      row.title || "未提供",
      existingTitles.join("、") || "未填写",
      Boolean(row.title) && !identityValueMatches(row.title, existingTitles),
    ),
  );
  table.append(tableHead, tableBody);
  comparison.append(table);

  const controls = element("div", "identity-resolution-controls");
  const action = createSelect(
    [
      ["", "请选择导师的任职情况"],
      ["append_current_affiliation", "同时在两个机构任职"],
      ["transfer_current_affiliation", "已经调到本次投稿的机构"],
    ],
    "identity-action",
    `${row.name}的任职处理方式`,
  );
  const actionField = labeledControl("任职情况", action);
  const makePrimary = document.createElement("input");
  makePrimary.type = "checkbox";
  makePrimary.setAttribute("aria-label", `将${row.name}的本次任职设为主要任职`);
  const primaryOption = element("label", "identity-primary-option");
  primaryOption.append(makePrimary, element("span", null, "将本次投稿的机构设为主要任职"));

  const formerOptions = [
    ["", "选择要结束的现有任职"],
    ...row.identity.mentor.affiliations.map((affiliation) => [
      affiliation.id,
      identityAffiliationLabel(affiliation),
    ]),
  ];
  const formerAffiliation = createSelect(
    formerOptions,
    "former-affiliation",
    `${row.name}调动前要结束的任职`,
  );
  const formerField = labeledControl(
    "哪条任职已经结束",
    formerAffiliation,
    "原任职会保留为历史记录。",
  );
  const reason = createInput("text", "例如：官网显示同时受聘于两个学院", "identity-reason");
  reason.setAttribute("aria-label", `${row.name}任职判定的审核依据`);
  reason.maxLength = 500;
  const reasonField = labeledControl("判断依据（必填）", reason);
  controls.append(actionField, primaryOption, formerField, reasonField);
  panel.append(heading, comparison, controls);
  return {
    panel,
    status,
    action,
    actionField,
    makePrimary,
    primaryOption,
    formerAffiliation,
    formerField,
    reason,
    reasonField,
    controls,
  };
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
  if (editor.identityPanel) {
    editor.identityAction.value = storedText(value.identity_action, 50);
    editor.identityMakePrimary.checked = Boolean(value.identity_make_primary);
    editor.identityFormerAffiliation.value = storedText(value.identity_former_affiliation_id, 80);
    editor.identityReason.value = storedText(value.identity_reason, 500);
  }
  editor.organizationInput.hidden = editor.action.value !== "map_existing";
  editor.reason.hidden = editor.action.value !== "reject";
}

function createRowEditor(row, card) {
  const existingEditor = state.rowEditorByProposalId.get(row.proposal_id);
  if (existingEditor) {
    return existingEditor;
  }
  const wrapper = element("div", "row-editor");
  const identity = element("div", "row-identity");
  const nameLine = element("div", "row-name-line");
  nameLine.append(element("strong", null, row.name), createMentorProfileButton(row));
  identity.append(nameLine, element("span", null, row.email));
  const action = createSelect(
    [
      ["follow", "跟随这组的最终归属"],
      ["map_existing", "单独调整到其他机构"],
      ["reject", "不收录这位导师"],
    ],
    "row-action",
    `${row.name}的逐行处理方式`,
  );
  const organizationInput = createOrganizationPicker(
    MentorReviewLogic.rankOrganizationCandidates(card.group, [
      ...state.manifest.organizations,
      ...state.pendingOrganizations,
    ]),
    "输入学校、学院或系所",
    `${row.name}单独调整到的机构`,
  );
  const reason = createInput("text", "拒绝原因", "row-reason");
  reason.setAttribute("aria-label", `拒绝${row.name}的原因`);
  reason.maxLength = 500;
  if (row.identity?.requires_resolution === true) {
    action.value = "reject";
    reason.value = IDENTITY_CONFLICT_REJECTION_REASON;
  }
  organizationInput.hidden = action.value !== "map_existing";
  reason.hidden = action.value !== "reject";
  const editor = {
    row,
    card,
    wrapper,
    action,
    organizationInput,
    reason,
    restoreTargetId: null,
    identityPanel: null,
  };
  if (row.identity?.requires_resolution === true) {
    const identityResolution = createIdentityResolutionPanel(row, card);
    wrapper.classList.add("has-identity-resolution");
    editor.identityPanel = identityResolution.panel;
    editor.identityStatus = identityResolution.status;
    editor.identityAction = identityResolution.action;
    editor.identityActionField = identityResolution.actionField;
    editor.identityMakePrimary = identityResolution.makePrimary;
    editor.identityPrimaryOption = identityResolution.primaryOption;
    editor.identityFormerAffiliation = identityResolution.formerAffiliation;
    editor.identityFormerField = identityResolution.formerField;
    editor.identityReason = identityResolution.reason;
    editor.identityReasonField = identityResolution.reasonField;
    editor.identityControls = identityResolution.controls;
    editor.identityTargetId = null;
  }
  action.addEventListener("change", () => {
    organizationInput.hidden = action.value !== "map_existing";
    reason.hidden = action.value !== "reject";
    updateIdentityResolutionState(editor);
    markGroupWorkflowChanged(card);
  });
  organizationInput.addEventListener("change", () => {
    updateIdentityResolutionState(editor);
    markGroupWorkflowChanged(card);
  });
  reason.addEventListener("input", () => markGroupWorkflowChanged(card));
  if (editor.identityPanel) {
    editor.identityAction.addEventListener("change", () => {
      updateIdentityResolutionState(editor);
      markGroupWorkflowChanged(card);
    });
    editor.identityMakePrimary.addEventListener("change", () => markGroupWorkflowChanged(card));
    editor.identityFormerAffiliation.addEventListener("change", () =>
      markGroupWorkflowChanged(card),
    );
    editor.identityReason.addEventListener("input", () => markGroupWorkflowChanged(card));
  }
  wrapper.append(identity, action, organizationInput, reason);
  if (editor.identityPanel) {
    wrapper.append(editor.identityPanel);
  }
  state.rowEditors.push(editor);
  state.rowEditorByProposalId.set(row.proposal_id, editor);
  restoreRowEditorValue(editor);
  updateIdentityResolutionState(editor);
  return editor;
}

function selectableOrganizationsForTypes(allowedTypes) {
  return [...state.selectableOrganizationById.values()]
    .filter((organization) => allowedTypes.has(organization.type))
    .sort((first, second) =>
      organizationLabel(first).localeCompare(organizationLabel(second), "zh-CN"),
    );
}

function parentTypesForOrganizationType(organizationType) {
  if (SCHOOL_ORGANIZATION_TYPES.has(organizationType)) {
    return new Set(["university"]);
  }
  if (DEPARTMENT_ORGANIZATION_TYPES.has(organizationType)) {
    return new Set(["school", "institute"]);
  }
  return new Set();
}

function groupParentDraftForTarget(card) {
  const organizationType = card.targetOrganizationType.value;
  const parentLevel = SCHOOL_ORGANIZATION_TYPES.has(organizationType)
    ? "university"
    : DEPARTMENT_ORGANIZATION_TYPES.has(organizationType)
      ? "school"
      : null;
  return parentLevel
    ? state.organizationDraftByKey.get(card.group.draftKeys?.[parentLevel]) || null
    : null;
}

function mappingKindForTarget(card, organization) {
  const inferredKind = MentorReviewLogic.correctionKindForDepartment(
    card.group.submitted.department,
    card.group.submitted.school,
  );
  if (inferredKind === "department_as_school" && organization?.type === "school") {
    return inferredKind;
  }
  if (inferredKind === "department_as_institute" && organization?.type === "institute") {
    return inferredKind;
  }
  return "custom";
}

function updateIndependentPanelVisibility(card) {
  const mode = card.mappingMode.value;
  const creating = card.targetAction.value === "create";
  const organizationType = card.targetOrganizationType.value;
  card.targetPanel.hidden = mode === "standard";
  card.targetExistingPanel.hidden = creating;
  card.targetCreatePanel.hidden = !creating;
  card.targetParentControls.hidden = organizationType === "university";
  card.targetOtherParentField.hidden =
    organizationType === "university" || card.targetParentMode.value !== "other";
  card.mappingReasonField.hidden = mode !== "corrected";
  card.savePathCorrectionLabel.hidden = mode !== "corrected";
}

function createIndependentTargetEditor(card) {
  const group = card.group;
  const resolvedSuggestion = card.pathSuggestion?.targetId
    ? {
        kind:
          card.pathSuggestion.correctionKind ||
          group.suggested_path_correction?.kind ||
          MentorReviewLogic.correctionKindForDepartment(
            group.submitted.department,
            group.submitted.school,
          ) ||
          "custom",
        target_organization_id: card.pathSuggestion.targetId,
        source: card.pathSuggestion.confidence === "certain" ? "history" : "heuristic",
        reason: card.pathSuggestion.reason,
      }
    : group.suggested_path_correction;
  const defaults = MentorReviewLogic.correctionDefaults(
    group.submitted,
    resolvedSuggestion,
  );
  const panel = element("section", "independent-target-panel");
  const groupContext = element("p", "independent-target-context");
  groupContext.append(
    element("span", null, "投稿路径"),
    element("strong", null, pathText(group)),
  );
  const notice = element("div", "path-correction-notice");
  if (defaults) {
    notice.dataset.source = defaults.source;
    notice.append(
      element(
        "strong",
        null,
        defaults.source === "history" ? "已处理过相同路径" : "可能填错层级",
      ),
      element("span", null, defaults.reason),
    );
  }
  notice.hidden = !defaults;

  const targetControls = element("div", "independent-target-controls");
  const targetAction = createSelect(
    [
      ["existing", "选择已有机构"],
      ["create", "新建机构"],
    ],
    "target-action",
    `${pathText(group)}的最终归属处理方式`,
  );
  const targetExistingPanel = element("div", "target-existing-panel");
  const targetExistingInput = createOrganizationPicker(
    MentorReviewLogic.rankOrganizationCandidates(group, state.manifest.organizations),
    "输入学校、学院或系所",
    `${pathText(group)}的最终归属机构`,
  );
  targetExistingPanel.append(labeledControl("最终归属", targetExistingInput));

  const targetCreatePanel = element("div", "target-create-panel");
  const targetOrganizationType = createSelect(
    ALL_ORGANIZATION_TYPES.map((value) => [value, TYPE_LABELS[value]]),
    "target-organization-type",
    `${pathText(group)}的新机构类型`,
  );
  const targetCanonicalName = createInput(
    "text",
    "新机构正式名称",
    "target-canonical-name",
  );
  targetCanonicalName.maxLength = 255;
  targetCanonicalName.setAttribute("aria-label", `${pathText(group)}的新机构正式名称`);
  const targetParentControls = element("div", "target-parent-controls");
  const targetParentMode = createSelect(
    [
      ["group", "沿用当前上级"],
      ["other", "选择其他上级"],
    ],
    "target-parent-mode",
    `${pathText(group)}的新机构上级来源`,
  );
  const targetOtherParentInput = createOrganizationPicker(
    [],
    "输入学校或学院",
    `${pathText(group)}的新机构上级`,
  );
  const targetOtherParentField = labeledControl("其他上级", targetOtherParentInput);
  targetParentControls.append(
    labeledControl("上级机构", targetParentMode),
    targetOtherParentField,
  );
  const targetOfficialUrl = createInput(
    "url",
    "官网（可留空）",
    "target-official-url",
  );
  targetOfficialUrl.maxLength = 500;
  targetOfficialUrl.setAttribute("aria-label", `${pathText(group)}的新机构官方网站`);
  const targetApprovedDomains = createInput(
    "text",
    "额外域名，多个用逗号分隔",
    "target-approved-domains",
  );
  targetApprovedDomains.maxLength = 2000;
  targetApprovedDomains.setAttribute("aria-label", `${pathText(group)}的新机构来源域名`);
  targetCreatePanel.append(
    labeledControl("机构类型", targetOrganizationType),
    labeledControl("正式名称", targetCanonicalName),
    targetParentControls,
    labeledControl("官网", targetOfficialUrl),
    labeledControl("额外域名", targetApprovedDomains),
  );
  targetControls.append(
    labeledControl("处理方式", targetAction),
    targetExistingPanel,
    targetCreatePanel,
  );

  const correctionControls = element("div", "path-correction-controls");
  const mappingReason = createInput(
    "text",
    "例如：导师主页显示其实际属于另一学院",
    "mapping-reason",
  );
  mappingReason.maxLength = 500;
  mappingReason.setAttribute("aria-label", `${pathText(group)}调整机构归属的判断依据`);
  const mappingReasonField = labeledControl("判断依据（必填）", mappingReason);
  const savePathCorrection = document.createElement("input");
  savePathCorrection.type = "checkbox";
  savePathCorrection.checked = true;
  savePathCorrection.setAttribute("aria-label", `记住${pathText(group)}的机构归属判断`);
  const savePathCorrectionLabel = element("label", "save-path-correction");
  savePathCorrectionLabel.append(
    savePathCorrection,
    element("span", null, "相同路径下次沿用"),
  );
  correctionControls.append(mappingReasonField, savePathCorrectionLabel);

  const targetPreview = element("p", "independent-target-preview", "未选择归属");
  const targetError = element("p", "independent-target-error error");
  targetError.hidden = true;
  panel.append(
    groupContext,
    notice,
    targetControls,
    correctionControls,
    targetPreview,
    targetError,
  );

  Object.assign(card, {
    targetPanel: panel,
    correctionNotice: notice,
    targetAction,
    targetExistingPanel,
    targetExistingInput,
    targetCreatePanel,
    targetOrganizationType,
    targetCanonicalName,
    targetParentControls,
    targetParentMode,
    targetOtherParentField,
    targetOtherParentInput,
    targetOfficialUrl,
    targetApprovedDomains,
    mappingReason,
    mappingReasonField,
    savePathCorrection,
    savePathCorrectionLabel,
    targetPreview,
    targetError,
    targetTypeManuallySelected: false,
    independentTargetId: null,
    independentCreation: null,
    independentPendingOrganization: null,
    restoreIndependentTargetId: defaults?.targetId || null,
    restoreIndependentParentId: null,
  });

  card.mappingMode.value = defaults?.mode || "standard";
  targetAction.value = defaults?.targetAction || "existing";
  targetCanonicalName.value =
    defaults?.canonicalName || String(group.submitted.department || group.submitted.school || "").trim();
  targetOrganizationType.value =
    defaults?.organizationType ||
    MentorReviewLogic.organizationTypeForCorrection("custom", targetCanonicalName.value);
  targetParentMode.value = defaults?.parentMode || "group";
  mappingReason.value = defaults?.reason || "";
  savePathCorrection.checked = defaults?.savePathCorrection !== false;
  if (defaults?.targetId && targetExistingInput.selectById(defaults.targetId)) {
    card.restoreIndependentTargetId = null;
  }

  const mark = () => {
    markGroupWorkflowChanged(card);
  };
  targetAction.addEventListener("change", mark);
  targetExistingInput.addEventListener("change", mark);
  targetOrganizationType.addEventListener("change", () => {
    card.targetTypeManuallySelected = true;
    mark();
  });
  targetCanonicalName.addEventListener("input", () => {
    if (!card.targetTypeManuallySelected) {
      targetOrganizationType.value = MentorReviewLogic.organizationTypeForCorrection(
        "custom",
        targetCanonicalName.value,
      );
    }
    mark();
  });
  targetParentMode.addEventListener("change", mark);
  targetOtherParentInput.addEventListener("change", mark);
  targetOfficialUrl.addEventListener("input", mark);
  targetApprovedDomains.addEventListener("input", mark);
  mappingReason.addEventListener("input", mark);
  savePathCorrection.addEventListener("change", mark);
  updateIndependentPanelVisibility(card);
  return panel;
}

function updateGroupCard(card) {
  updateIndependentPanelVisibility(card);
  const rejecting = card.groupAction.value === "reject";
  card.assignment.hidden = false;
  card.groupReason.hidden = !rejecting;
  if (rejecting) {
    card.assignment.textContent =
      card.mappingMode.value === "alternate" && card.independentTargetId
        ? `不收录整组；个别导师归入「${organizationPathForId(card.independentTargetId)}」。`
        : "不收录整组；可单独保留导师。";
    card.article.dataset.kind = "rejected";
    return;
  }
  card.article.dataset.kind = "resolved";
  const targetId = groupTargetId(card);
  if (!targetId) {
    card.assignment.textContent = "等待机构确认";
    return;
  }
  const drafts = LEVELS.map((level) =>
    state.organizationDraftByKey.get(card.group.draftKeys?.[level]),
  ).filter(Boolean);
  const pending = drafts.filter((draft) => draftIsActive(draft) && !draft.confirmed);
  const prefix = "归属";
  const alternate =
    card.mappingMode.value === "alternate" && card.independentTargetId
      ? ` · 个别导师：${organizationPathForId(card.independentTargetId)}`
      : "";
  card.assignment.textContent = pending.length
    ? `${prefix}：${organizationPathForId(targetId)} · ${pending.length} 个机构待处理${alternate}`
    : `${prefix}：${organizationPathForId(targetId)}${alternate}`;
}

function pathSuggestionForGroup(group) {
  const suggestion = MentorReviewLogic.pathReviewSuggestion(
    group.submitted,
    group.suggested_path_correction,
  );
  if (suggestion?.action === "use_ancestor") {
    return {
      ...suggestion,
      ancestorDraftKey: group.draftKeys?.[suggestion.targetLevel] || null,
      correctionKind: "custom",
    };
  }
  if (suggestion?.action !== "review_hierarchy") {
    return suggestion;
  }

  const identityEvidence = MentorReviewLogic.identitySchoolEvidence(
    group.rows,
    state.manifest.organizations,
  );
  const identityOrganization = identityEvidence?.organization;
  const identityUniversity = identityOrganization?.lineage_names?.[0];
  if (
    identityOrganization &&
    MentorReviewLogic.compactOrganizationName(identityUniversity) ===
      MentorReviewLogic.compactOrganizationName(group.submitted.university)
  ) {
    const names = [
      identityOrganization.canonical_name,
      ...(identityOrganization.aliases || []),
    ];
    const parentScore = Math.max(
      0,
      ...names.map((name) =>
        MentorReviewLogic.schoolOrganizationNameScore(
          group.submitted.school,
          name,
          [group.submitted.university],
        ),
      ),
    );
    const reason =
      `现有同邮箱导师中，${identityEvidence.votes} 位的任职指向` +
      `「${identityOrganization.canonical_name}」。`;
    if (parentScore >= 95) {
      return {
        ...suggestion,
        action: "use_parent",
        targetLevel: "school",
        confidence: "high",
        title: `归入「${group.submitted.school}」`,
        reason,
      };
    }
    return {
      ...suggestion,
      action: "use_existing",
      targetId: identityOrganization.id,
      correctionKind: MentorReviewLogic.correctionKindForDepartment(
        group.submitted.department,
        group.submitted.school,
      ),
      confidence: "high",
      title: `移到「${identityOrganization.canonical_name}」`,
      reason,
    };
  }

  const draftByCandidateId = new Map();
  const draftCandidates = state.organizationDrafts
    .filter((draft) => draft.level === "school" && draft.key !== group.draftKeys?.school)
    .map((draft) => {
      const parent = draft.parentKey
        ? state.organizationDraftByKey.get(draft.parentKey)
        : null;
      const candidate = {
        id: draft.key,
        type: draft.editor?.organizationType.value || inferOrganizationType("school", draft.submittedName),
        canonical_name: draft.submittedName,
        aliases: [],
        lineage_names: [parent?.submittedName, draft.submittedName].filter(Boolean),
      };
      draftByCandidateId.set(candidate.id, draft);
      return candidate;
    });
  const placement = MentorReviewLogic.schoolLevelPlacementDefault(
    group.submitted,
    [...state.manifest.organizations, ...draftCandidates],
  );

  if (placement?.action === "use_existing") {
    const siblingDraft = draftByCandidateId.get(placement.organization.id);
    return {
      ...suggestion,
      action: siblingDraft ? "use_sibling_draft" : "use_existing",
      ...(siblingDraft
        ? { siblingDraftKey: siblingDraft.key }
        : { targetId: placement.organization.id }),
      correctionKind: placement.correctionKind,
      confidence: "high",
      title: `移到「${placement.organization.canonical_name}」`,
      reason:
        placement.score === 100
          ? `名称与${siblingDraft ? "本次投稿中的" : "同校已有"}学院一致。`
          : `名称与${siblingDraft ? "本次投稿中的" : "同校已有"}学院高度相近。`,
    };
  }

  if (placement?.action === "use_parent") {
    return {
      ...suggestion,
      action: "use_parent",
      targetLevel: "school",
      confidence: placement.confidence,
      title: `归入「${group.submitted.school}」`,
      reason: placement.reason,
    };
  }
  if (placement?.action === "reject_group") {
    return {
      ...suggestion,
      action: "reject_group",
      correctionKind: placement.correctionKind,
      confidence: "review",
      title: group.rows.length === 1 ? "不收录这位导师" : "不收录这组",
      reason: placement.reason,
    };
  }
  return {
    ...suggestion,
    action: "create_sibling",
    correctionKind: placement?.correctionKind,
    confidence: "review",
    canonicalName: placement?.canonicalName || group.submitted.department,
    organizationType: placement?.organizationType || "school",
    title: `新建「${group.submitted.department}」并迁入`,
    reason: `名称像学院或研究院，但未找到可信的同名或别名。已预填为${group.submitted.university}直属机构，请核对。`,
  };
}

function openGroupRejection(card) {
  card.groupAction.value = "reject";
  card.mappingMode.value = "standard";
  card.advancedDetails.open = true;
  updateGroupCard(card);
  markGroupWorkflowChanged(card, "请填写不收录原因。");
  window.setTimeout(() => card.groupReason.focus(), 0);
}

function createGroupCard(group) {
  const article = element("article", "review-group");
  const header = element("div", "group-header");
  const titleArea = element("div");
  titleArea.append(element("h3", null, pathText(group)));
  const badges = element("div", "group-badges");
  badges.append(element("span", "badge", `${group.rows.length} 位导师`));
  const identityRows = group.rows.filter((row) => row.identity?.requires_resolution === true);
  if (identityRows.length) {
    badges.append(element("span", "badge identity-badge", `${identityRows.length} 项任职待判断`));
  }
  header.append(titleArea, badges);

  const pathSuggestion = pathSuggestionForGroup(group);
  const requiresPathReview = Boolean(
    pathSuggestion && pathSuggestion.confidence !== "certain",
  );
  const requiresAttention = requiresPathReview || identityRows.length > 0;
  const decisionSummary = element("section", "group-decision-summary");
  const decisionCopy = element("div", "group-decision-copy");
  decisionCopy.append(
    element(
      "strong",
      "group-decision-title",
      pathSuggestion?.title || (identityRows.length ? "确认任职关系" : "沿用投稿路径"),
    ),
  );
  if (pathSuggestion?.reason) {
    decisionCopy.append(element("p", "group-decision-reason", pathSuggestion.reason));
  }
  const quickActions = element("div", "group-quick-actions");
  decisionSummary.append(decisionCopy, quickActions);

  const sources = sourceLinks(group);
  const groupControls = element("div", "group-controls");
  const groupAction = createSelect(
    [
      ["resolve", "收录这组导师"],
      ["reject", "不收录这组导师"],
    ],
    "group-action",
    `${pathText(group)}的整组处理方式`,
  );
  const mappingMode = createSelect(
    [
      ["standard", "按投稿路径"],
      ["corrected", "整组调整归属"],
      ["alternate", "仅调整个别导师"],
    ],
    "mapping-mode",
    `${pathText(group)}的导师归属处理方式`,
  );
  const groupReason = createInput("text", "拒绝原因", "group-reason");
  groupReason.setAttribute("aria-label", `拒绝${pathText(group)}的原因`);
  groupReason.maxLength = 500;
  groupReason.hidden = true;
  groupControls.append(groupAction, mappingMode, groupReason);
  const assignment = element("p", "group-assignment", "机构尚未确认");

  const ordinaryRows = group.rows.filter((row) => row.identity?.requires_resolution !== true);
  const rowsDetails = element("details", "rows-details");
  const rowsSummary = element("summary", null, `逐位调整导师（${ordinaryRows.length}）`);
  const rowsContainer = element("div", "rows-container");
  const loadMoreRows = element("button", "text-button rows-load-more", "继续加载导师");
  loadMoreRows.type = "button";
  rowsDetails.append(rowsSummary, rowsContainer);

  const card = {
    group,
    article,
    header,
    pathSuggestion,
    requiresAttention,
    workflowConfirmed: !requiresAttention,
    completedAt: null,
    independentValidationError: null,
    decisionSummary,
    quickActions,
    groupAction,
    mappingMode,
    groupReason,
    assignment,
    rowEditors: [],
    renderedRowCount: 0,
    ordinaryRowCount: ordinaryRows.length,
    rowsDetails,
  };
  const targetPanel = createIndependentTargetEditor(card);
  const advancedDetails = element("details", "group-advanced-details");
  const advancedSummary = element("summary", null, "调整归属或不收录");
  const advancedBody = element("div", "group-advanced-body");
  advancedBody.append(groupControls, targetPanel);
  advancedDetails.append(advancedSummary, advancedBody);
  card.advancedDetails = advancedDetails;

  if (pathSuggestion?.action === "use_parent") {
    const useParent = element(
      "button",
      "primary-button compact-button",
      pathSuggestion.title,
    );
    useParent.type = "button";
    card.suggestionActionButton = useParent;
    card.suggestionActionLabel = pathSuggestion.title;
    useParent.addEventListener("click", () => void applySuggestedGroupDecision(card));
    quickActions.append(useParent);
    const keepPath = element("button", "secondary-button compact-button", "保留原层级");
    keepPath.type = "button";
    keepPath.addEventListener("click", () => void keepSubmittedGroupPath(card));
    const chooseOther = element("button", "secondary-button compact-button", "选择其他机构");
    chooseOther.type = "button";
    chooseOther.addEventListener("click", () => openGroupAdjustment(card));
    const reject = element(
      "button",
      "text-button compact-button danger-button",
      group.rows.length === 1 ? "不收录这位导师" : "不收录这组",
    );
    reject.type = "button";
    reject.addEventListener("click", () => openGroupRejection(card));
    quickActions.append(chooseOther, keepPath, reject);
  } else if (pathSuggestion?.action === "reject_group") {
    const reject = element(
      "button",
      "primary-button compact-button",
      pathSuggestion.title,
    );
    reject.type = "button";
    card.suggestionActionButton = reject;
    card.suggestionActionLabel = pathSuggestion.title;
    reject.addEventListener("click", () => void applySuggestedGroupDecision(card));
    const chooseOther = element("button", "secondary-button compact-button", "选择其他机构");
    chooseOther.type = "button";
    chooseOther.addEventListener("click", () => openGroupAdjustment(card));
    const keepPath = element("button", "secondary-button compact-button", "确为下级系所");
    keepPath.type = "button";
    keepPath.addEventListener("click", () => void keepSubmittedGroupPath(card));
    quickActions.append(reject, chooseOther, keepPath);
  } else if (
    ["use_existing", "use_sibling_draft", "use_ancestor", "create_sibling"].includes(
      pathSuggestion?.action,
    )
  ) {
    const useExisting = element(
      "button",
      "primary-button compact-button",
      pathSuggestion.title || "采用已有归属",
    );
    useExisting.type = "button";
    card.suggestionActionButton = useExisting;
    card.suggestionActionLabel = pathSuggestion.title || "采用已有归属";
    useExisting.addEventListener("click", () => void applySuggestedGroupDecision(card));
    quickActions.append(useExisting);
    const chooseOther = element("button", "secondary-button compact-button", "选择其他机构");
    chooseOther.type = "button";
    chooseOther.addEventListener("click", () => openGroupAdjustment(card));
    if (pathSuggestion.action === "create_sibling") {
      const useParent = element(
        "button",
        "secondary-button compact-button",
        `归入「${group.submitted.school}」`,
      );
      useParent.type = "button";
      useParent.addEventListener("click", () =>
        void applySuggestedGroupDecision(card, "use_parent"),
      );
      quickActions.append(useParent);
    }
    const keepPath = element(
      "button",
      "secondary-button compact-button",
      pathSuggestion.action === "create_sibling" ? "确为下级系所" : "保留原层级",
    );
    keepPath.type = "button";
    keepPath.addEventListener("click", () => void keepSubmittedGroupPath(card));
    quickActions.append(chooseOther, keepPath);
    const reject = element(
      "button",
      "text-button compact-button danger-button",
      group.rows.length === 1 ? "不收录这位导师" : "不收录这组",
    );
    reject.type = "button";
    reject.addEventListener("click", () => openGroupRejection(card));
    quickActions.append(reject);
  } else if (pathSuggestion?.action === "review_hierarchy") {
    const chooseOther = element(
      "button",
      "primary-button compact-button",
      pathSuggestion.kind === "ambiguous_hierarchy" &&
        group.submitted.department.endsWith("学院")
        ? "移到其他学院"
        : "选择正确机构",
    );
    chooseOther.type = "button";
    chooseOther.addEventListener("click", () => openGroupAdjustment(card));
    const keepPath = element("button", "secondary-button compact-button", "确为下级系所");
    keepPath.type = "button";
    keepPath.addEventListener("click", () => void keepSubmittedGroupPath(card));
    const reject = element(
      "button",
      "text-button compact-button danger-button",
      group.rows.length === 1 ? "不收录这位导师" : "不收录这组",
    );
    reject.type = "button";
    reject.addEventListener("click", () => openGroupRejection(card));
    quickActions.append(chooseOther, keepPath, reject);
  }

  if (identityRows.length) {
    const identitySection = element("section", "identity-conflicts");
    const heading = element("div", "identity-conflicts-heading");
    heading.append(element("strong", null, "确认同邮箱导师的任职"));
    const container = element("div", "identity-conflicts-list");
    for (const row of identityRows) {
      const editor = createRowEditor(row, card);
      card.rowEditors.push(editor);
      container.append(editor.wrapper);
    }
    identitySection.append(heading, container);
    card.identitySection = identitySection;
  }
  card.loadMoreRows = () => {
    const end = Math.min(card.renderedRowCount + 100, ordinaryRows.length);
    const fragment = document.createDocumentFragment();
    for (const row of ordinaryRows.slice(card.renderedRowCount, end)) {
      const editor = createRowEditor(row, card);
      card.rowEditors.push(editor);
      fragment.append(editor.wrapper);
    }
    card.renderedRowCount = end;
    rowsContainer.append(fragment);
    loadMoreRows.textContent = `继续加载（剩余 ${ordinaryRows.length - end} 位）`;
    loadMoreRows.hidden = end >= ordinaryRows.length;
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
  groupAction.addEventListener("change", () => {
    if (groupAction.value === "reject") {
      mappingMode.value = "standard";
    }
    markGroupWorkflowChanged(card);
  });
  mappingMode.addEventListener("change", () => markGroupWorkflowChanged(card));
  groupReason.addEventListener("input", () => markGroupWorkflowChanged(card));

  const taskFooter = element("footer", "group-task-footer");
  const taskStatus = element(
    "p",
    "group-task-status",
    "",
  );
  taskFooter.append(taskStatus);
  Object.assign(card, { taskFooter, taskStatus });
  setGroupTaskStatus(card, taskStatus.textContent);

  article.append(header, decisionSummary, sources, assignment, advancedDetails);
  if (card.identitySection) {
    article.append(card.identitySection);
  }
  if (ordinaryRows.length) {
    article.append(rowsDetails);
  }
  article.append(taskFooter);
  updateGroupCard(card);
  return card;
}

function setGroupTaskStatus(card, message) {
  card.taskStatus.textContent = message;
  card.taskStatus.hidden = !message;
  card.article.dataset.workflowStatus = card.workflowConfirmed ? "done" : "pending";
}

function markGroupWorkflowChanged(card, message = "修改后请确认。") {
  card.workflowConfirmed = false;
  clearWorkflowCompletion(card);
  if (state.workflowFilter === "done") {
    state.workflowFilter = "all";
  }
  if (card.taskStatus) {
    setGroupTaskStatus(card, message);
  }
  scheduleReviewUpdate();
}

function identityReviewError(card) {
  for (const row of card.group.rows) {
    if (row.identity?.requires_resolution !== true) {
      continue;
    }
    const editor = state.rowEditorByProposalId.get(row.proposal_id);
    if (!editor || editor.action.value === "reject" || card.groupAction.value === "reject") {
      continue;
    }
    const targetId = effectiveRowTargetId(card, editor);
    if (targetId && isCurrentIdentityOrganization(editor, targetId)) {
      continue;
    }
    if (!editor.identityAction.value) {
      return `${row.name}的任职关系尚未选择`;
    }
    if (!editor.identityReason.value.trim()) {
      return `${row.name}的任职判断需要填写依据`;
    }
    if (
      editor.identityAction.value === "transfer_current_affiliation" &&
      !editor.identityFormerAffiliation.value
    ) {
      return `${row.name}调动时需要选择原来的任职`;
    }
  }
  return null;
}

function suggestionClusterKey(card) {
  const suggestion = card.pathSuggestion;
  if (!suggestion || !["certain", "high"].includes(suggestion.confidence)) {
    return null;
  }
  if (suggestion.action === "use_existing" && suggestion.targetId) {
    return `existing:${suggestion.targetId}`;
  }
  if (suggestion.action === "use_sibling_draft" && suggestion.siblingDraftKey) {
    return `sibling:${suggestion.siblingDraftKey}`;
  }
  if (suggestion.action === "use_ancestor" && suggestion.ancestorDraftKey) {
    return `ancestor:${suggestion.ancestorDraftKey}`;
  }
  if (suggestion.action === "use_parent") {
    return [
      "parent",
      MentorReviewLogic.compactOrganizationName(card.group.submitted.university),
      MentorReviewLogic.compactOrganizationName(card.group.submitted.school),
    ].join(":");
  }
  return null;
}

function suggestionClusterCards(card) {
  const key = suggestionClusterKey(card);
  if (!key) {
    return [card];
  }
  return state.cards.filter(
    (candidate) =>
      suggestionClusterKey(candidate) === key &&
      (candidate === card || !candidate.workflowConfirmed),
  );
}

function updateSuggestionBatchLabels() {
  for (const card of state.cards) {
    if (!card.suggestionActionButton) {
      continue;
    }
    const count = suggestionClusterCards(card).length;
    card.suggestionActionButton.textContent =
      count > 1
        ? `${card.suggestionActionLabel}（${count} 组）`
        : card.suggestionActionLabel;
  }
}

function configureSuggestedGroupControls(card, forcedAction = null, { manual = false } = {}) {
  const suggestion = card.pathSuggestion;
  const action = forcedAction || suggestion?.action;
  card.groupAction.value = "resolve";
  let configured = true;
  if (action === "use_existing" && suggestion?.targetId) {
    card.mappingMode.value = "corrected";
    card.targetAction.value = "existing";
    card.mappingReason.value = suggestion.reason;
    configured = card.targetExistingInput.selectById(suggestion.targetId);
  } else if (action === "use_sibling_draft" && suggestion?.siblingDraftKey) {
    const siblingDraft = state.organizationDraftByKey.get(suggestion.siblingDraftKey);
    card.mappingMode.value = "corrected";
    card.targetAction.value = "existing";
    card.mappingReason.value = suggestion.reason;
    configured = Boolean(
      siblingDraft?.targetId && card.targetExistingInput.selectById(siblingDraft.targetId),
    );
  } else if (action === "use_ancestor" && suggestion?.ancestorDraftKey) {
    const ancestorDraft = state.organizationDraftByKey.get(suggestion.ancestorDraftKey);
    card.mappingMode.value = "corrected";
    card.targetAction.value = "existing";
    card.mappingReason.value = suggestion.reason;
    configured = Boolean(
      ancestorDraft?.targetId && card.targetExistingInput.selectById(ancestorDraft.targetId),
    );
  } else if (action === "create_sibling") {
    card.mappingMode.value = "corrected";
    card.targetAction.value = "create";
    card.targetOrganizationType.value = suggestion.organizationType;
    card.targetCanonicalName.value = suggestion.canonicalName;
    card.targetParentMode.value = "group";
    card.mappingReason.value = suggestion.reason;
    card.targetTypeManuallySelected = false;
  } else if (action === "reject_group") {
    card.groupAction.value = "reject";
    card.mappingMode.value = "standard";
    card.groupReason.value = suggestion.reason;
  } else if (action === "use_parent") {
    card.mappingMode.value = "standard";
    const departmentDraft = state.organizationDraftByKey.get(
      card.group.draftKeys?.department,
    );
    if (departmentDraft) {
      departmentDraft.editor.action.value = "skip";
      departmentDraft.editor.actionManuallySelected = manual;
      departmentDraft.editor.autoSkippedToParent = false;
      departmentDraft.editor.suggestedSkipToParent = !manual;
      departmentDraft.confirmed = true;
      departmentDraft.editor.error.hidden = true;
      departmentDraft.editor.defaultNotice.textContent = "已默认归入上级";
      departmentDraft.editor.defaultNotice.hidden = manual;
    }
  } else {
    configured = false;
  }
  updateGroupCard(card);
  return configured;
}

function applySuggestedGroupState(card, forcedAction = null) {
  const configured = configureSuggestedGroupControls(card, forcedAction, { manual: true });
  if (!configured) {
    card.workflowConfirmed = false;
    clearWorkflowCompletion(card);
    card.advancedDetails.open = true;
    updateIndependentPanelVisibility(card);
    setGroupTaskStatus(card, "请确认最终归属。");
    return false;
  }
  card.workflowConfirmed = !card.group.rows.some(
    (row) => row.identity?.requires_resolution === true,
  );
  if (card.workflowConfirmed) {
    markWorkflowCompleted(card);
  } else {
    clearWorkflowCompletion(card);
  }
  setGroupTaskStatus(
    card,
    card.workflowConfirmed
      ? ""
      : "继续确认任职。",
  );
  return true;
}

function applyInitialPathSuggestions() {
  let changed = false;
  for (const card of state.cards) {
    if (!card.pathSuggestion) {
      continue;
    }
    const configured = configureSuggestedGroupControls(card);
    if (!configured) {
      card.workflowConfirmed = false;
      clearWorkflowCompletion(card);
      setGroupTaskStatus(card, "请确认最终归属。");
      continue;
    }
    changed = true;
  }
  if (changed) {
    state.autosaveDirty = true;
  }
  return changed;
}

async function applySuggestedGroupDecision(card, forcedAction = null) {
  const cards = forcedAction ? [card] : suggestionClusterCards(card);
  for (const candidate of cards) {
    applySuggestedGroupState(candidate, forcedAction);
  }
  state.autosaveDirty = true;
  await updateOrganizationDrafts();
  if (cards.every((candidate) => candidate.workflowConfirmed)) {
    focusNextWorkflowTask();
  }
}

async function keepSubmittedGroupPath(card) {
  card.groupAction.value = "resolve";
  card.mappingMode.value = "standard";
  const departmentDraft = state.organizationDraftByKey.get(
    card.group.draftKeys?.department,
  );
  if (departmentDraft && departmentDraft.editor.action.value === "skip") {
    departmentDraft.editor.action.value = "create";
    departmentDraft.editor.actionManuallySelected = true;
    departmentDraft.editor.autoSkippedToParent = false;
    departmentDraft.editor.suggestedSkipToParent = false;
    departmentDraft.confirmed = false;
  }
  card.workflowConfirmed = !card.group.rows.some(
    (row) => row.identity?.requires_resolution === true,
  );
  if (card.workflowConfirmed) {
    markWorkflowCompleted(card);
  } else {
    clearWorkflowCompletion(card);
  }
  setGroupTaskStatus(
    card,
    card.workflowConfirmed
      ? ""
      : "继续确认任职。",
  );
  state.autosaveDirty = true;
  await updateOrganizationDrafts();
  if (card.workflowConfirmed) {
    focusNextWorkflowTask();
  }
}

function openGroupAdjustment(card) {
  card.groupAction.value = "resolve";
  card.mappingMode.value = "corrected";
  card.targetAction.value = "existing";
  card.advancedDetails.open = true;
  updateGroupCard(card);
  markGroupWorkflowChanged(card, "请选择归属并填写依据。");
  window.setTimeout(() => card.targetExistingInput.focus(), 0);
}

async function confirmGroupWorkflowTask(card, { advance = true } = {}) {
  card.taskStatus.classList.remove("error");
  if (card.groupAction.value === "reject" && !card.groupReason.value.trim()) {
    card.taskStatus.textContent = "请填写不收录这组导师的原因。";
    card.taskStatus.classList.add("error");
    card.advancedDetails.open = true;
    card.groupReason.focus();
    return false;
  }
  if (card.mappingMode.value !== "standard") {
    const error = await updateIndependentTargetCard(card, true);
    if (error) {
      card.taskStatus.textContent = error;
      card.taskStatus.classList.add("error");
      card.advancedDetails.open = true;
      return false;
    }
  }
  const identityError = identityReviewError(card);
  if (identityError) {
    card.taskStatus.textContent = identityError;
    card.taskStatus.classList.add("error");
    return false;
  }
  card.workflowConfirmed = true;
  markWorkflowCompleted(card);
  setGroupTaskStatus(card, "");
  state.autosaveDirty = true;
  await updateOrganizationDrafts();
  if (advance) {
    focusNextWorkflowTask();
  }
  return true;
}

function groupWorkflowValidationError(card) {
  if (card.groupAction.value === "reject" && !card.groupReason.value.trim()) {
    return "请填写不收录这组导师的原因。";
  }
  if (
    card.mappingMode.value !== "standard" &&
    card.independentValidationError
  ) {
    return card.independentValidationError;
  }
  return identityReviewError(card);
}

function workflowTaskStatus(task) {
  if (task.kind === "organization") {
    return task.draft.confirmed ? "done" : "pending";
  }
  return task.card.workflowConfirmed && !groupWorkflowValidationError(task.card)
    ? "done"
    : "pending";
}

function workflowSortMetadata(group, phase) {
  const university = String(group?.submitted?.university || "未填写学校").trim();
  const school = String(group?.submitted?.school || "").trim();
  const department = String(group?.submitted?.department || "").trim();
  return {
    sortPath: [university, school, department],
    sectionLabel: [university, school].filter(Boolean).join(" / "),
    phase,
    batchRow: Math.min(...(group?.rows || []).map((row) => row.batch_row), 5_001),
  };
}

function representativeGroupForDraft(draft) {
  return state.cards
    .filter((card) => draft.groupIds.has(card.group.id))
    .map((card) => card.group)
    .sort((first, second) =>
      pathText(first).localeCompare(pathText(second), "zh-CN") ||
      Math.min(...first.rows.map((row) => row.batch_row)) -
        Math.min(...second.rows.map((row) => row.batch_row)),
    )[0] || null;
}

function compareWorkflowTaskPath(first, second) {
  for (let index = 0; index < first.sortPath.length; index += 1) {
    const order = first.sortPath[index].localeCompare(second.sortPath[index], "zh-CN");
    if (order) {
      return order;
    }
  }
  return (
    first.phase - second.phase ||
    first.batchRow - second.batchRow ||
    first.title.localeCompare(second.title, "zh-CN") ||
    first.id.localeCompare(second.id)
  );
}

function buildWorkflowTasks() {
  const tasks = [];
  for (const card of state.cards) {
    const validationError = groupWorkflowValidationError(card);
    if (card.workflowConfirmed && validationError) {
      card.workflowConfirmed = false;
      clearWorkflowCompletion(card);
      setGroupTaskStatus(card, validationError);
    }
  }
  const unresolvedPathDraftKeys = new Set(
    state.cards
      .filter((card) => card.pathSuggestion && !card.workflowConfirmed)
      .map((card) => card.group.draftKeys?.department)
      .filter(Boolean),
  );
  for (const card of state.cards) {
    const hasIdentityReview = card.group.rows.some(
      (row) => row.identity?.requires_resolution === true,
    );
    const label = hasIdentityReview
      ? "任职"
      : card.pathSuggestion
        ? "归属"
        : "导师";
    const phase = card.pathSuggestion ? 2 : hasIdentityReview ? 4 : 5;
    tasks.push({
      id: `group:${card.group.id}`,
      kind: "group",
      label,
      title: card.pathSuggestion?.title || "沿用投稿路径",
      subtitle: taskContext(card.group),
      card,
      element: card.article,
      completedAt: card.completedAt,
      ...workflowSortMetadata(card.group, phase),
    });
  }
  for (const draft of state.organizationDrafts) {
    if (
      !draftIsActive(draft) ||
      draft.forcedSkip ||
      unresolvedPathDraftKeys.has(draft.key)
    ) {
      continue;
    }
    const representativeGroup = representativeGroupForDraft(draft);
    const phase = { university: 0, school: 1, department: 3 }[draft.level];
    tasks.push({
      id: `organization:${draft.key}`,
      kind: "organization",
      label: {
        university: "学校",
        school: "学院",
        department: "系所",
      }[draft.level],
      title: draft.submittedName,
      subtitle: `${draft.groupIds.size} 组 · ${draft.rowCount} 位导师`,
      draft,
      element: draft.editor.details,
      completedAt: draft.completedAt,
      ...workflowSortMetadata(representativeGroup, phase),
    });
  }
  tasks.sort(compareWorkflowTaskPath);
  return tasks;
}

function workflowNodeIdForDraft(draft) {
  return `node:${draft.key}`;
}

function buildWorkflowNodes() {
  const nodeByDraftKey = new Map();
  const nodeById = new Map();
  const taskNodeIds = new Map();
  for (const draft of state.organizationDrafts) {
    const node = {
      id: workflowNodeIdForDraft(draft),
      name: draft.submittedName,
      level: draft.level,
      draft,
      parent: null,
      children: [],
      cards: [],
      tasks: [],
      pathNames: [],
      searchText: "",
      pendingTasks: [],
      completedAt: null,
      subtreeCompletedAt: null,
      subtreePendingNodes: 0,
      subtreePendingTasks: 0,
      subtreeDoneNodes: 0,
      sortIndex: 0,
      directVisible: false,
      treeVisible: false,
    };
    nodeByDraftKey.set(draft.key, node);
    nodeById.set(node.id, node);
  }

  const roots = [];
  for (const node of nodeById.values()) {
    const parent = node.draft?.parentKey
      ? nodeByDraftKey.get(node.draft.parentKey) || null
      : null;
    node.parent = parent;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
    const task = state.workflowTaskById.get(`organization:${node.draft.key}`);
    if (task) {
      node.tasks.push(task);
      taskNodeIds.set(task.id, node.id);
    }
  }

  for (const card of state.cards) {
    const draftKey = [...LEVELS]
      .reverse()
      .map((level) => card.group.draftKeys?.[level])
      .find((key) => key && nodeByDraftKey.has(key));
    let node = draftKey ? nodeByDraftKey.get(draftKey) : null;
    if (!node) {
      const submittedPath = LEVELS.map((level) => card.group.submitted[level]).filter(Boolean);
      node = {
        id: `node:group:${card.group.id}`,
        name: submittedPath.at(-1) || "未填写机构",
        level: submittedPath.length >= 3 ? "department" : submittedPath.length === 2 ? "school" : "university",
        draft: null,
        parent: null,
        children: [],
        cards: [],
        tasks: [],
        pathNames: submittedPath,
        searchText: "",
        pendingTasks: [],
        completedAt: null,
        subtreeCompletedAt: null,
        subtreePendingNodes: 0,
        subtreePendingTasks: 0,
        subtreeDoneNodes: 0,
        sortIndex: 0,
        directVisible: false,
        treeVisible: false,
      };
      roots.push(node);
      nodeById.set(node.id, node);
    }
    node.cards.push(card);
    const task = state.workflowTaskById.get(`group:${card.group.id}`);
    if (task) {
      node.tasks.push(task);
      taskNodeIds.set(task.id, node.id);
    }
  }

  const sortNodes = (nodesToSort) => {
    nodesToSort.sort(
      (first, second) =>
        first.name.localeCompare(second.name, "zh-CN") || first.id.localeCompare(second.id),
    );
    for (const node of nodesToSort) {
      sortNodes(node.children);
    }
  };
  sortNodes(roots);

  const ordered = [];
  const prepareNode = (node) => {
    node.tasks.sort((first, second) => first.phase - second.phase || first.id.localeCompare(second.id));
    if (!node.pathNames.length) {
      const names = [];
      let current = node;
      while (current) {
        names.unshift(current.name);
        current = current.parent;
      }
      node.pathNames = names;
    }
    const mentorValues = node.cards.flatMap((card) =>
      card.group.rows.flatMap((row) => [row.name, row.email, row.title || ""]),
    );
    node.searchText = [
      ...node.pathNames,
      node.draft?.submittedName || "",
      ...mentorValues,
    ]
      .join(" ")
      .normalize("NFKC")
      .toLocaleLowerCase();
    node.sortIndex = ordered.length;
    ordered.push(node);
    for (const child of node.children) {
      prepareNode(child);
    }
  };
  for (const root of roots) {
    prepareNode(root);
  }

  const updateStats = (node) => {
    node.pendingTasks = node.tasks.filter(
      (task) => workflowTaskStatus(task) === "pending",
    );
    node.completedAt = Math.max(
      0,
      ...node.tasks.map((task) => task.completedAt || 0),
    ) || null;
    node.subtreePendingNodes = node.pendingTasks.length ? 1 : 0;
    node.subtreePendingTasks = node.pendingTasks.length;
    node.subtreeDoneNodes = node.tasks.length && !node.pendingTasks.length ? 1 : 0;
    node.subtreeCompletedAt = node.completedAt;
    for (const child of node.children) {
      updateStats(child);
      node.subtreePendingNodes += child.subtreePendingNodes;
      node.subtreePendingTasks += child.subtreePendingTasks;
      node.subtreeDoneNodes += child.subtreeDoneNodes;
      node.subtreeCompletedAt = Math.max(
        node.subtreeCompletedAt || 0,
        child.subtreeCompletedAt || 0,
      ) || null;
    }
  };
  for (const root of roots) {
    updateStats(root);
  }
  return { ordered, roots, nodeById, taskNodeIds };
}

function workflowNodeMatchesFilter(node) {
  if (!node.tasks.length) {
    return false;
  }
  if (state.workflowFilter === "pending") {
    return node.pendingTasks.length > 0;
  }
  if (state.workflowFilter === "done") {
    return node.pendingTasks.length === 0;
  }
  return true;
}

function workflowSearchTokens() {
  return state.workflowSearchQuery
    .normalize("NFKC")
    .toLocaleLowerCase()
    .split(/\s+/u)
    .map((token) => token.trim())
    .filter(Boolean);
}

function workflowNodeMatchesSearch(node, tokens = workflowSearchTokens()) {
  return !tokens.length || tokens.every((token) => node.searchText.includes(token));
}

function updateWorkflowTreeVisibility() {
  const tokens = workflowSearchTokens();
  const visit = (node) => {
    node.directVisible = workflowNodeMatchesFilter(node) && workflowNodeMatchesSearch(node, tokens);
    let childVisible = false;
    for (const child of node.children) {
      childVisible = visit(child) || childVisible;
    }
    node.treeVisible = node.directVisible || childVisible;
    return node.treeVisible;
  };
  for (const root of state.workflowRootNodes) {
    visit(root);
  }
}

function visibleWorkflowNodes() {
  const visible = state.workflowNodes.filter((node) => node.directVisible);
  if (state.workflowFilter !== "done") {
    return visible;
  }
  return visible.sort(
    (first, second) =>
      (second.completedAt || 0) - (first.completedAt || 0) ||
      first.sortIndex - second.sortIndex,
  );
}

function expandWorkflowNodePath(node) {
  let current = node;
  while (current) {
    state.expandedWorkflowNodeIds.add(current.id);
    current = current.parent;
  }
}

function workflowChildrenForDisplay(node) {
  const visible = node.children.filter((child) => child.treeVisible);
  if (state.workflowFilter !== "done") {
    return visible;
  }
  return visible.sort(
    (first, second) =>
      (second.subtreeCompletedAt || 0) - (first.subtreeCompletedAt || 0) ||
      first.name.localeCompare(second.name, "zh-CN"),
  );
}

function formatCompletionTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function renderWorkflowTree() {
  nodes.organizationTree.replaceChildren();
  let selectedButton = null;
  const searchActive = Boolean(workflowSearchTokens().length);
  const renderBranch = (node, depth) => {
    const item = element("li", `review-tree-item tree-level-${node.level}`);
    item.setAttribute("role", "treeitem");
    item.setAttribute("aria-level", String(depth));
    item.setAttribute("aria-selected", String(node.id === state.currentWorkflowNodeId));
    const children = workflowChildrenForDisplay(node);
    const expanded = searchActive || state.expandedWorkflowNodeIds.has(node.id);
    if (children.length) {
      item.setAttribute("aria-expanded", String(expanded));
    }
    const row = element("div", "review-tree-row");
    if (children.length) {
      const toggle = element("button", "review-tree-toggle", "›");
      toggle.type = "button";
      toggle.setAttribute("aria-label", `${expanded ? "折叠" : "展开"}${node.name}`);
      toggle.setAttribute("aria-expanded", String(expanded));
      if (expanded) {
        toggle.dataset.expanded = "true";
      }
      toggle.addEventListener("click", () => {
        if (state.expandedWorkflowNodeIds.has(node.id)) {
          state.expandedWorkflowNodeIds.delete(node.id);
        } else {
          state.expandedWorkflowNodeIds.add(node.id);
        }
        renderWorkflowTree();
      });
      row.append(toggle);
    } else {
      row.append(element("span", "review-tree-toggle-spacer"));
    }

    const button = element("button", "review-tree-node");
    button.type = "button";
    button.dataset.status =
      state.workflowFilter === "done"
        ? "done"
        : node.pendingTasks.length || node.subtreePendingNodes
          ? "pending"
          : "done";
    button.setAttribute("aria-current", String(node.id === state.currentWorkflowNodeId));
    const identity = element("span", "review-tree-node-identity");
    identity.append(
      element(
        "span",
        "review-tree-node-level",
        { university: "学校", school: "学院", department: "系所" }[node.level],
      ),
      element("strong", null, node.name),
    );
    const details = element("span", "review-tree-node-details");
    const mentorCount = node.draft?.rowCount ||
      node.cards.reduce((total, card) => total + card.group.rows.length, 0);
    if (mentorCount) {
      details.append(element("span", null, `${mentorCount} 位导师`));
    }
    if (state.workflowFilter === "done" && node.completedAt) {
      details.append(element("time", null, formatCompletionTime(node.completedAt)));
    }
    const hasPendingDescendants =
      node.subtreePendingNodes > (node.pendingTasks.length ? 1 : 0);
    const pendingLabel =
      state.workflowFilter === "done"
        ? node.directVisible && node.subtreeDoneNodes === 1
          ? "完成"
          : `${node.subtreeDoneNodes} 个`
        : hasPendingDescendants
          ? `${node.subtreePendingNodes} 处`
          : node.pendingTasks.length
            ? `${node.pendingTasks.length} 项`
            : "完成";
    details.append(element("span", "review-tree-node-status", pendingLabel));
    button.append(identity, details);
    button.addEventListener("click", () => selectWorkflowNode(node.id));
    row.append(button);
    item.append(row);
    if (children.length && expanded) {
      const group = element("ul", "review-tree-group");
      group.setAttribute("role", "group");
      for (const child of children) {
        group.append(renderBranch(child, depth + 1));
      }
      item.append(group);
    }
    if (node.id === state.currentWorkflowNodeId) {
      selectedButton = button;
    }
    return item;
  };

  const roots = state.workflowRootNodes
    .filter((root) => root.treeVisible)
    .sort((first, second) => {
      if (state.workflowFilter === "done") {
        const recentOrder =
          (second.subtreeCompletedAt || 0) - (first.subtreeCompletedAt || 0);
        if (recentOrder) {
          return recentOrder;
        }
      }
      return first.name.localeCompare(second.name, "zh-CN");
    });
  if (!roots.length) {
    const empty = element(
      "p",
      "review-tree-empty",
      state.workflowSearchQuery ? "没有匹配的机构或导师" : "当前没有机构节点",
    );
    nodes.organizationTree.append(empty);
    return;
  }
  const list = element("ul", "review-tree-list");
  list.setAttribute("role", "group");
  for (const root of roots) {
    list.append(renderBranch(root, 1));
  }
  nodes.organizationTree.append(list);
  if (selectedButton) {
    window.requestAnimationFrame(() => {
      if (selectedButton.isConnected) {
        selectedButton.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    });
  }
}

function workbenchSection(title, detail, content, status) {
  const section = element("section", "node-workbench-section");
  section.dataset.status = status;
  const heading = element("header", "node-workbench-section-heading");
  const copy = element("div");
  copy.append(element("h3", null, title));
  if (detail) {
    copy.append(element("p", null, detail));
  }
  heading.append(
    copy,
    element(
      "span",
      "node-workbench-section-status",
      status === "pending" ? "待处理" : "已处理",
    ),
  );
  section.append(heading, content);
  return section;
}

function revealWorkflowMentorMatch(node) {
  document
    .querySelectorAll(".has-tree-search-match")
    .forEach((matched) => matched.classList.remove("has-tree-search-match"));
  const tokens = workflowSearchTokens();
  if (!tokens.length) {
    return;
  }
  for (const card of node.cards) {
    const row = card.group.rows.find((candidate) => {
      const searchText = [candidate.name, candidate.email, candidate.title || ""]
        .join(" ")
        .normalize("NFKC")
        .toLocaleLowerCase();
      return tokens.every((token) => searchText.includes(token));
    });
    if (!row) {
      continue;
    }
    if (card.rowsDetails) {
      card.rowsDetails.open = true;
    }
    let editor = state.rowEditorByProposalId.get(row.proposal_id);
    while (
      !editor &&
      card.loadMoreRows &&
      card.renderedRowCount < card.ordinaryRowCount
    ) {
      card.loadMoreRows();
      editor = state.rowEditorByProposalId.get(row.proposal_id);
    }
    if (editor) {
      editor.wrapper.classList.add("has-tree-search-match");
      window.requestAnimationFrame(() => {
        if (editor.wrapper.isConnected) {
          editor.wrapper.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    }
    return;
  }
}

function renderWorkflowNode(node) {
  const activeControl = nodes.taskStage.contains(document.activeElement)
    ? document.activeElement
    : null;
  const selection =
    activeControl instanceof HTMLInputElement || activeControl instanceof HTMLTextAreaElement
      ? [activeControl.selectionStart, activeControl.selectionEnd]
      : null;
  nodes.taskEmpty.hidden = Boolean(node);
  nodes.nodeContent.replaceChildren();
  if (!node) {
    nodes.workflowPosition.textContent = "当前没有节点";
    nodes.previousTask.disabled = true;
    nodes.confirmTask.disabled = true;
    nodes.confirmTask.textContent = "确认此节点并继续";
    nodes.nextTask.disabled = true;
    return;
  }

  const header = element("header", "review-node-header");
  const headingCopy = element("div", "review-node-heading-copy");
  headingCopy.append(
    element("span", "review-node-eyebrow", "投稿路径"),
    element("h2", null, node.name),
    element("p", "review-node-breadcrumb", node.pathNames.join(" / ")),
  );
  const summary = element("div", "review-node-summary");
  summary.append(
    element(
      "span",
      "review-node-level-badge",
      { university: "学校", school: "学院", department: "系所" }[node.level],
    ),
    element(
      "strong",
      node.pendingTasks.length ? "pending" : "done",
      node.pendingTasks.length ? `${node.pendingTasks.length} 项待处理` : "已处理",
    ),
  );
  header.append(headingCopy, summary);
  nodes.nodeContent.append(header);

  const organizationTask = node.tasks.find((task) => task.kind === "organization");
  if (organizationTask) {
    organizationTask.element.open = true;
    nodes.nodeContent.append(
      workbenchSection(
        "机构信息",
        "确认名称、类型和官网",
        organizationTask.element,
        workflowTaskStatus(organizationTask),
      ),
    );
  }
  for (const task of node.tasks.filter((candidate) => candidate.kind === "group")) {
    const identityCount = task.card.group.rows.filter(
      (row) => row.identity?.requires_resolution === true,
    ).length;
    const sectionTitle = task.card.pathSuggestion
      ? "归属与导师"
      : identityCount
        ? "任职与导师"
        : "导师";
    nodes.nodeContent.append(
      workbenchSection(
        sectionTitle,
        `${task.card.group.rows.length} 位导师${identityCount ? ` · ${identityCount} 项任职待判断` : ""}`,
        task.element,
        workflowTaskStatus(task),
      ),
    );
  }
  if (!node.tasks.length) {
    nodes.nodeContent.append(
      element("p", "review-node-context-note", "请从下级节点继续审核。"),
    );
  }
  revealWorkflowMentorMatch(node);

  const visible = visibleWorkflowNodes();
  const index = visible.findIndex((item) => item.id === node.id);
  nodes.workflowPosition.textContent =
    index >= 0 ? `${index + 1} / ${visible.length} 个节点` : "层级节点";
  nodes.previousTask.disabled = index <= 0;
  nodes.confirmTask.disabled = node.pendingTasks.length === 0;
  nodes.confirmTask.textContent = node.pendingTasks.length
    ? "确认此节点并继续"
    : "此节点已确认";
  nodes.nextTask.disabled = index < 0 || index >= visible.length - 1;
  nodes.nextTask.textContent = node.pendingTasks.length ? "暂时跳过" : "下一个";
  if (activeControl?.isConnected) {
    activeControl.focus({ preventScroll: true });
    if (selection && typeof activeControl.setSelectionRange === "function") {
      activeControl.setSelectionRange(selection[0], selection[1]);
    }
  }
}

function setWorkflowFilter(filter) {
  state.workflowFilter = filter;
  state.currentWorkflowTaskId = null;
  state.currentWorkflowNodeId = null;
  refreshWorkflowTasks();
}

function selectWorkflowNode(nodeId, { focus = true, preferredTaskId = null } = {}) {
  const node = state.workflowNodeById.get(nodeId);
  if (!node) {
    return;
  }
  if (state.workflowSearchQuery && !workflowNodeMatchesSearch(node)) {
    state.workflowSearchQuery = "";
    nodes.treeSearch.value = "";
    updateWorkflowTreeVisibility();
  }
  state.currentWorkflowNodeId = node.id;
  const preferred = node.tasks.find((task) => task.id === preferredTaskId);
  state.currentWorkflowTaskId =
    preferred?.id || node.pendingTasks[0]?.id || node.tasks[0]?.id || null;
  expandWorkflowNodePath(node);
  renderWorkflowTree();
  renderWorkflowNode(node);
  if (focus) {
    nodes.taskStage.focus({ preventScroll: true });
  }
}

function selectWorkflowTask(taskId, { focus = true } = {}) {
  const nodeId = state.workflowNodeIdByTaskId.get(taskId);
  if (nodeId) {
    selectWorkflowNode(nodeId, { focus, preferredTaskId: taskId });
  }
}

function updateWorkflowProgress() {
  const pendingTasks = state.workflowTasks.filter(
    (task) => workflowTaskStatus(task) === "pending",
  ).length;
  const pendingNodes = state.workflowNodes.filter(
    (node) => node.pendingTasks.length > 0,
  ).length;
  nodes.workflowPendingCount.textContent = String(pendingNodes);
  nodes.generate.disabled = pendingTasks > 0;
  nodes.generate.textContent = pendingTasks
    ? `还有 ${pendingNodes} 个节点待处理`
    : "生成审核评论";
}

function refreshWorkflowTasks() {
  updateSuggestionBatchLabels();
  state.workflowTasks = buildWorkflowTasks();
  state.workflowTaskById = new Map(state.workflowTasks.map((task) => [task.id, task]));
  const tree = buildWorkflowNodes();
  state.workflowNodes = tree.ordered;
  state.workflowRootNodes = tree.roots;
  state.workflowNodeById = tree.nodeById;
  state.workflowNodeIdByTaskId = tree.taskNodeIds;
  updateWorkflowTreeVisibility();

  const pendingCount = state.workflowNodes.filter((node) => node.pendingTasks.length).length;
  const doneCount = state.workflowNodes.filter(
    (node) => node.tasks.length && !node.pendingTasks.length,
  ).length;
  const allCount = state.workflowNodes.filter((node) => node.tasks.length).length;
  nodes.taskFilterPendingCount.textContent = String(pendingCount);
  nodes.taskFilterDoneCount.textContent = String(doneCount);
  nodes.taskFilterAllCount.textContent = String(allCount);
  for (const [button, filter] of [
    [nodes.taskFilterPending, "pending"],
    [nodes.taskFilterDone, "done"],
    [nodes.taskFilterAll, "all"],
  ]) {
    button.setAttribute("aria-selected", String(state.workflowFilter === filter));
  }

  const visible = visibleWorkflowNodes();
  let current = state.workflowNodeById.get(state.currentWorkflowNodeId);
  if (!current || !visible.some((node) => node.id === current.id)) {
    current = visible[0] || null;
    state.currentWorkflowNodeId = current?.id || null;
  }
  if (current) {
    expandWorkflowNodePath(current);
    if (!current.tasks.some((task) => task.id === state.currentWorkflowTaskId)) {
      state.currentWorkflowTaskId = current.pendingTasks[0]?.id || current.tasks[0]?.id || null;
    }
  } else {
    state.currentWorkflowTaskId = null;
  }
  renderWorkflowTree();
  renderWorkflowNode(current);
  updateWorkflowProgress();
}

function focusNextWorkflowTask(afterNodeId = state.currentWorkflowNodeId) {
  const previousIndex = state.workflowNodeById.get(afterNodeId)?.sortIndex ?? -1;
  refreshWorkflowTasks();
  const pending = state.workflowNodes.filter((node) => node.pendingTasks.length > 0);
  if (!pending.length) {
    state.workflowFilter = "done";
    state.currentWorkflowNodeId = null;
    refreshWorkflowTasks();
    nodes.decisionPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    nodes.generate.focus({ preventScroll: true });
    return;
  }
  state.workflowFilter = "pending";
  updateWorkflowTreeVisibility();
  const sameNode = state.workflowNodeById.get(afterNodeId);
  const target =
    (sameNode?.pendingTasks.length ? sameNode : null) ||
    pending.find((node) => node.sortIndex > previousIndex) ||
    pending[0];
  selectWorkflowNode(target.id);
  updateWorkflowProgress();
}

function moveWorkflowTask(offset) {
  const visible = visibleWorkflowNodes();
  const index = visible.findIndex((node) => node.id === state.currentWorkflowNodeId);
  const target = visible[index + offset];
  if (target) {
    selectWorkflowNode(target.id);
  }
}

async function confirmCurrentWorkflowTask() {
  const node = state.workflowNodeById.get(state.currentWorkflowNodeId);
  if (!node?.pendingTasks.length) {
    return;
  }
  const nodeId = node.id;
  const taskIds = node.pendingTasks.map((task) => task.id);
  for (const taskId of taskIds) {
    const task = state.workflowTaskById.get(taskId);
    if (!task || workflowTaskStatus(task) !== "pending") {
      continue;
    }
    const confirmed =
      task.kind === "organization"
        ? await confirmOrganizationDraft(task.draft, { advance: false })
        : await confirmGroupWorkflowTask(task.card, { advance: false });
    if (!confirmed) {
      selectWorkflowTask(taskId, { focus: false });
      return;
    }
  }
  focusNextWorkflowTask(nodeId);
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
    clearWorkflowCompletion(affected);
    affected.editor.error.hidden = true;
  }
  if (state.workflowFilter === "done") {
    state.workflowFilter = "all";
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

function requiredSubmittedLevelsForCard(card) {
  if (card.groupAction.value === "reject") {
    return new Set();
  }
  if (
    card.mappingMode.value === "corrected" &&
    card.targetAction.value === "existing"
  ) {
    const targetId = parseOrganizationInput(card.targetExistingInput);
    if (!targetId) {
      return new Set(LEVELS);
    }
    const target = state.selectableOrganizationById.get(targetId);
    if (target?.pending_kind === "hierarchy") {
      const lineageIds = new Set(target.lineage_ids || []);
      return new Set(
        LEVELS.filter((level) => {
          const draft = state.organizationDraftByKey.get(card.group.draftKeys?.[level]);
          return Boolean(draft?.targetId && lineageIds.has(draft.targetId));
        }),
      );
    }
  }
  return new Set(
    MentorReviewLogic.requiredSubmittedLevels({
      mappingMode: card.mappingMode.value,
      targetAction: card.targetAction.value,
      parentMode: card.targetParentMode.value,
      organizationType: card.targetOrganizationType.value,
    }),
  );
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

function registerPendingOrganization(organization) {
  state.pendingOrganizationIds.add(organization.id);
  state.selectableOrganizationById.set(organization.id, organization);
  const label = organizationLabel(organization);
  state.organizationLabelById.set(organization.id, label);
  state.organizationIdByLabel.set(label, organization.id);
}

function mergePendingOrganization(byId, organization) {
  const existing = byId.get(organization.id);
  if (!existing) {
    byId.set(organization.id, organization);
    return;
  }
  existing.approved_domains = [
    ...new Set([...(existing.approved_domains || []), ...(organization.approved_domains || [])]),
  ].sort();
  existing.official_urls = [
    ...new Set([...(existing.official_urls || []), ...(organization.official_urls || [])]),
  ];
}

function showIndependentTargetError(card, message) {
  card.targetError.textContent = message || "";
  card.targetError.hidden = !message;
  card.targetPanel.classList.toggle("has-error", Boolean(message));
}

async function updateIndependentTargetCard(card, showErrors = false) {
  updateIndependentPanelVisibility(card);
  card.independentTargetId = null;
  card.independentCreation = null;
  card.independentPendingOrganization = null;
  if (card.mappingMode.value === "standard") {
    card.independentValidationError = null;
    card.targetPreview.textContent = "按投稿路径";
    showIndependentTargetError(card, null);
    return null;
  }

  let error = null;
  let targetOrganization = null;
  if (card.targetAction.value === "existing") {
    const targetId = parseOrganizationInput(card.targetExistingInput);
    targetOrganization = state.selectableOrganizationById.get(targetId) || null;
    if (!targetOrganization) {
      error = "请选择归属机构";
    } else {
      card.independentTargetId = targetOrganization.id;
    }
  } else if (card.targetAction.value === "create") {
    const organizationType = card.targetOrganizationType.value;
    const canonicalName = card.targetCanonicalName.value.trim();
    if (!ALL_ORGANIZATION_TYPES.includes(organizationType)) {
      error = "新机构类型无效";
    } else if (!canonicalName) {
      error = "新机构需要填写正式名称";
    }

    let parentId = null;
    let parent = null;
    if (!error && organizationType !== "university") {
      if (card.targetParentMode.value === "group") {
        const parentDraft = groupParentDraftForTarget(card);
        parentId = parentDraft?.targetId || null;
      } else {
        parentId = parseOrganizationInput(card.targetOtherParentInput);
      }
      parent = state.selectableOrganizationById.get(parentId) || null;
      const allowedParentTypes = parentTypesForOrganizationType(organizationType);
      if (!parent || !allowedParentTypes.has(parent.type)) {
        error = SCHOOL_ORGANIZATION_TYPES.has(organizationType)
          ? "新学院或研究院需要选择大学作为上级"
          : "新系所、中心或实验室需要选择学院或研究院作为上级";
      }
    }

    let officialUrl = null;
    let approvedDomains = [];
    if (!error) {
      const officialUrlValue = card.targetOfficialUrl.value.trim();
      try {
        officialUrl = officialUrlValue
          ? validateWebUrl(officialUrlValue, `新机构「${canonicalName}」的官网`)
          : null;
        approvedDomains = parseDomains(card.targetApprovedDomains.value);
      } catch (caught) {
        error = caught instanceof Error ? caught.message : "新机构官网或域名无效";
      }
    }
    const effectiveDomains = [
      ...new Set([...(parent?.approved_domains || []), ...approvedDomains]),
    ];
    if (!error && organizationType === "university" && !officialUrl) {
      error = `新学校「${canonicalName}」需要填写官方网站`;
    }
    if (!error && organizationType === "university" && approvedDomains.length === 0) {
      error = `新学校「${canonicalName}」至少需要一个官方来源域名`;
    }
    if (officialUrl && !sourceUrlMatchesDomains(officialUrl, effectiveDomains)) {
      error = `新机构「${canonicalName}」的官网不属于本级或上级官方来源域名`;
    }
    if (!error) {
      const targetId = await proposedOrganizationId(
        organizationType,
        canonicalName,
        parentId,
      );
      const lineageIds = [...(parent?.lineage_ids || []), targetId];
      const lineageNames = [...(parent?.lineage_names || []), canonicalName];
      card.independentTargetId = targetId;
      card.independentCreation = {
        organization_id: targetId,
        organization_type: organizationType,
        canonical_name: canonicalName,
        parent_id: parentId,
        official_url: officialUrl,
        approved_domains: approvedDomains,
      };
      card.independentPendingOrganization = {
        id: targetId,
        type: organizationType,
        canonical_name: canonicalName,
        parent_id: parentId,
        aliases: [],
        official_urls: officialUrl ? [officialUrl] : [],
        approved_domains: effectiveDomains,
        lineage_ids: lineageIds,
        lineage_names: lineageNames,
        pending: true,
        pending_kind: "independent",
      };
      targetOrganization = card.independentPendingOrganization;
    }
  } else {
    error = "请选择归属处理方式";
  }

  if (!error && card.mappingMode.value === "corrected" && card.groupAction.value === "reject") {
    error = "不收录整组时，不能同时调整整组归属。";
  }
  if (!error && card.mappingMode.value === "corrected" && !card.mappingReason.value.trim()) {
    error = "请填写判断依据";
  }

  card.targetPreview.textContent = targetOrganization
    ? `${card.mappingMode.value === "corrected" ? "整组归入" : "个别导师归入"}：${(
        targetOrganization.lineage_names || [targetOrganization.canonical_name]
      ).join(" / ")}`
    : "未选择归属";
  card.independentValidationError = error;
  if (error && card.workflowConfirmed) {
    card.workflowConfirmed = false;
    clearWorkflowCompletion(card);
    setGroupTaskStatus(card, "归属已失效，请重新确认。");
  }
  showIndependentTargetError(card, showErrors ? error : null);
  return error;
}

async function refreshPendingOrganizationOptions() {
  const targetSelections = new Map(
    state.cards.map((card) => [
      card,
      card.targetExistingInput.selectedId || card.restoreIndependentTargetId,
    ]),
  );
  const parentSelections = new Map(
    state.cards.map((card) => [
      card,
      card.targetOtherParentInput.selectedId || card.restoreIndependentParentId,
    ]),
  );
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
      pending_kind: "hierarchy",
    };
    mergePendingOrganization(byId, organization);
  }
  for (const organization of byId.values()) {
    registerPendingOrganization(organization);
  }

  const preliminaryOptions = [...state.manifest.organizations, ...byId.values()];
  for (const card of state.cards) {
    const parentOptions = preliminaryOptions.filter(
      (organization) =>
        organization.pending_kind !== "independent" &&
        parentTypesForOrganizationType(card.targetOrganizationType.value).has(organization.type),
    );
    card.targetOtherParentInput.setOptions(parentOptions);
    const preservedParentId = parentSelections.get(card);
    if (preservedParentId && card.targetOtherParentInput.selectById(preservedParentId)) {
      card.restoreIndependentParentId = null;
    }
    if (card.targetAction.value === "create") {
      await updateIndependentTargetCard(card);
    }
    if (card.independentPendingOrganization) {
      mergePendingOrganization(byId, card.independentPendingOrganization);
    }
  }

  removePendingOrganizationOptions();
  state.pendingOrganizations = [...byId.values()].sort((first, second) =>
    organizationLabel(first).localeCompare(organizationLabel(second), "zh-CN"),
  );
  for (const organization of state.pendingOrganizations) {
    registerPendingOrganization(organization);
  }

  const options = [...state.manifest.organizations, ...state.pendingOrganizations];
  for (const card of state.cards) {
    card.targetExistingInput.setOptions(
      MentorReviewLogic.rankOrganizationCandidates(card.group, options),
    );
    const preservedTargetId = targetSelections.get(card);
    if (preservedTargetId && card.targetExistingInput.selectById(preservedTargetId)) {
      card.restoreIndependentTargetId = null;
    }
    const parentOptions = options.filter(
      (organization) =>
        organization.pending_kind !== "independent" &&
        parentTypesForOrganizationType(card.targetOrganizationType.value).has(organization.type),
    );
    card.targetOtherParentInput.setOptions(parentOptions);
    const preservedParentId = parentSelections.get(card);
    if (preservedParentId && card.targetOtherParentInput.selectById(preservedParentId)) {
      card.restoreIndependentParentId = null;
    }
    await updateIndependentTargetCard(card);
  }
  const signature = JSON.stringify(
    state.pendingOrganizations.map((organization) => [
      organization.id,
      organization.canonical_name,
      organization.parent_id,
      organization.approved_domains,
    ]),
  );
  if (signature !== state.pendingOrganizationsSignature) {
    state.pendingOrganizationsSignature = signature;
    for (const editor of state.rowEditors) {
      editor.organizationInput.setOptions(
        MentorReviewLogic.rankOrganizationCandidates(editor.card.group, options),
      );
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
  const activeDraftKeys = new Set();
  for (const card of state.cards) {
    const requiredLevels = requiredSubmittedLevelsForCard(card);
    for (const level of requiredLevels) {
      const key = card.group.draftKeys?.[level];
      if (key) {
        activeDraftKeys.add(key);
      }
    }
  }
  for (const draft of state.organizationDrafts) {
    draft.active = activeDraftKeys.has(draft.key);
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
        draft.autoMatchedExistingId = null;
        draft.autoMatchedExistingReason = null;
        initialExistingId = findExactOrganization(draft.level, parentId, draft.submittedName);
        if (!initialExistingId && draft.level === "school") {
          const identityEvidence = identitySchoolEvidenceForDraft(draft);
          if (
            identityEvidence &&
            organizationsForLevel(draft.level, parentId).some(
              (organization) => organization.id === identityEvidence.organization.id,
            )
          ) {
            initialExistingId = identityEvidence.organization.id;
            draft.autoMatchedExistingId = initialExistingId;
            draft.autoMatchedExistingReason = "identity";
          }
        }
        if (!initialExistingId && draft.level === "school") {
          const parentOrganization = state.selectableOrganizationById.get(parentId);
          const matched = MentorReviewLogic.schoolOrganizationCandidateMatch(
            draft.submittedName,
            organizationsForLevel(draft.level, parentId),
            parentOrganization?.lineage_names || [parentOrganization?.canonical_name],
          );
          if (matched) {
            initialExistingId = matched.organization.id;
            draft.autoMatchedExistingId = initialExistingId;
            draft.autoMatchedExistingReason = "name";
          }
        }
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
    editor.defaultNotice.hidden = true;
    if (
      draft.autoMatchedExistingId &&
      editor.action.value === "existing" &&
      editor.existingInput.selectedId === draft.autoMatchedExistingId
    ) {
      const matched = state.organizationById.get(draft.autoMatchedExistingId);
      editor.defaultNotice.textContent =
        draft.autoMatchedExistingReason === "identity"
          ? `同邮箱导师的现有任职指向「${matched?.canonical_name || draft.submittedName}」，已预选`
          : `名称明确匹配「${matched?.canonical_name || draft.submittedName}」，已预选`;
      editor.defaultNotice.hidden = false;
    }
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
        editor.reuseNotice.textContent = `已匹配「${organization?.canonical_name || draft.submittedName}」`;
        editor.reuseNotice.hidden = false;
      }
    }

    const parentCanonicalName = parent?.lineageNames?.length
      ? parent.lineageNames[parent.lineageNames.length - 1]
      : "";
    const shouldAutoSkip =
      !draft.forcedSkip &&
      !editor.actionManuallySelected &&
      editor.action.value !== "existing" &&
      shouldDefaultOrganizationToParent(
        draft.level,
        editor.canonicalName.value,
        parentCanonicalName,
      );
    if (editor.suggestedSkipToParent && editor.action.value === "skip") {
      editor.autoSkippedToParent = false;
      editor.defaultNotice.textContent = "已按审核证据预选归入上级";
      editor.defaultNotice.hidden = false;
    } else if (shouldAutoSkip) {
      if (editor.action.value !== "skip") {
        editor.action.value = "skip";
        draft.confirmed = true;
      }
      editor.autoSkippedToParent = true;
      editor.defaultNotice.textContent = "与上级同名，已归入上级";
      editor.defaultNotice.hidden = false;
    } else if (editor.autoSkippedToParent) {
      if (editor.action.value === "skip") {
        editor.action.value = "create";
        draft.confirmed = false;
      }
      editor.autoSkippedToParent = false;
    }

    editor.action.disabled = draft.forcedSkip;
    const action = draft.forcedSkip ? "skip" : editor.action.value;
    editor.existingPanel.hidden = action !== "existing";
    editor.createPanel.hidden = action !== "create";
    editor.aliasLabel.hidden = action === "skip" || !draft.submittedName;
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
        ? "已归入上级"
        : draft.confirmed
          ? "已处理"
          : "待处理";
    editor.status.textContent = statusText;
    editor.details.dataset.status = statusText;
    if (!active) {
      editor.details.open = false;
    }
  }
  if (token !== state.updateToken) {
    return;
  }
  await refreshPendingOrganizationOptions();
  if (token !== state.updateToken) {
    return;
  }
  for (const card of state.cards) {
    updateGroupCard(card);
  }
  for (const editor of state.rowEditors) {
    updateIdentityResolutionState(editor);
  }
  refreshWorkflowTasks();
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

async function confirmOrganizationDraft(draft, { advance = true } = {}) {
  await updateOrganizationDrafts();
  const message = showDraftValidation(draft);
  if (message) {
    draft.editor.details.open = true;
    return false;
  }
  draft.confirmed = true;
  markWorkflowCompleted(draft);
  state.autosaveDirty = true;
  draft.editor.error.hidden = true;
  draft.editor.details.classList.remove("has-error");
  draft.editor.details.open = false;
  await updateOrganizationDrafts();
  if (advance) {
    focusNextWorkflowTask();
  }
  return true;
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
    if (editor.identityPanel) {
      value.identity_action = editor.identityAction.value;
      value.identity_make_primary = editor.identityMakePrimary.checked;
      value.identity_former_affiliation_id = editor.identityFormerAffiliation.value || null;
      value.identity_reason = editor.identityReason.value;
    }
    if (
      value.action === "follow" &&
      !value.organization_id &&
      !value.reason &&
      !value.identity_action &&
      !value.identity_make_primary &&
      !value.identity_former_affiliation_id &&
      !value.identity_reason
    ) {
      delete rows[editor.row.proposal_id];
    } else {
      rows[editor.row.proposal_id] = value;
    }
  }
  return {
    version: 4,
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
          completed_at: draft.completedAt,
        },
      ]),
    ),
    groups: Object.fromEntries(
      state.cards.map((card) => [
        card.group.id,
        {
          action: card.groupAction.value,
          reason: card.groupReason.value,
          mapping_mode: card.mappingMode.value,
          target_action: card.targetAction.value,
          target_existing_id: parseOrganizationInput(card.targetExistingInput),
          target_organization_type: card.targetOrganizationType.value,
          target_canonical_name: card.targetCanonicalName.value,
          target_parent_mode: card.targetParentMode.value,
          target_parent_id: parseOrganizationInput(card.targetOtherParentInput),
          target_official_url: card.targetOfficialUrl.value,
          target_approved_domains: card.targetApprovedDomains.value,
          mapping_reason: card.mappingReason.value,
          save_path_correction: card.savePathCorrection.checked,
          workflow_confirmed: card.workflowConfirmed,
          completed_at: card.completedAt,
        },
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
    nodes.autosaveStatus.textContent = "已保存";
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

function storedCompletionTime(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : null;
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
  if (!saved || ![1, 2, 3, 4].includes(saved.version)) {
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
    if (saved.version >= 4) {
      draft.completedAt = storedCompletionTime(value.completed_at);
      state.lastCompletionAt = Math.max(state.lastCompletionAt, draft.completedAt || 0);
    }
    draft.editor.actionManuallySelected = true;
    draft.editor.organizationTypeManuallySelected = true;
    draft.editor.autoSkippedToParent = false;
    draft.editor.suggestedSkipToParent = false;
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
    if (saved.version >= 2) {
      card.mappingMode.value = storedText(value.mapping_mode, 30);
      card.targetAction.value = storedText(value.target_action, 20);
      card.targetOrganizationType.value = storedText(value.target_organization_type, 30);
      card.targetCanonicalName.value = storedText(value.target_canonical_name, 255);
      card.targetParentMode.value = storedText(value.target_parent_mode, 20);
      card.targetOfficialUrl.value = storedText(value.target_official_url, 500);
      card.targetApprovedDomains.value = storedText(value.target_approved_domains, 2000);
      card.mappingReason.value = storedText(value.mapping_reason, 500);
      card.savePathCorrection.checked = Boolean(value.save_path_correction);
      card.targetTypeManuallySelected = true;
      card.restoreIndependentTargetId = storedText(value.target_existing_id, 80) || null;
      card.restoreIndependentParentId = storedText(value.target_parent_id, 80) || null;
    }
    if (saved.version >= 3) {
      card.workflowConfirmed = Boolean(value.workflow_confirmed);
      setGroupTaskStatus(card, "");
    }
    if (saved.version >= 4) {
      card.completedAt = storedCompletionTime(value.completed_at);
      state.lastCompletionAt = Math.max(state.lastCompletionAt, card.completedAt || 0);
    }
    updateIndependentPanelVisibility(card);
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
        identity_action: storedText(value.identity_action, 50),
        identity_make_primary: Boolean(value.identity_make_primary),
        identity_former_affiliation_id:
          storedText(value.identity_former_affiliation_id, 80) || null,
        identity_reason: storedText(value.identity_reason, 500),
      };
      if (
        restored.action !== "follow" ||
        restored.organization_id ||
        restored.reason ||
        restored.identity_action ||
        restored.identity_make_primary ||
        restored.identity_former_affiliation_id ||
        restored.identity_reason
      ) {
        state.restoredRowValues.set(row.proposal_id, restored);
      }
    }
  }
  for (const editor of state.rowEditors) {
    restoreRowEditorValue(editor);
  }
  nodes.autosaveStatus.textContent = "已恢复上次进度";
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

async function collectLevels(group, requiredLevels = new Set(LEVELS)) {
  const card = state.groupCardById.get(group.id) || null;
  if (requiredLevels.size === 0) {
    return { levels: [], targetId: null, effectiveDomains: [] };
  }
  const levels = [];
  let parentId = null;
  let inheritedDomains = [];
  let skippedSchool = false;
  let targetId = null;
  for (const level of LEVELS) {
    if (!requiredLevels.has(level)) {
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
    const draft = state.organizationDraftByKey.get(group.draftKeys?.[level]);
    if (!draft) {
      if (level === "university") {
        throw new ReviewValidationError(`“${pathText(group)}”缺少学校名称`, { card });
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
      throw new ReviewValidationError(`${LEVEL_LABELS[level]}「${draft.submittedName}」尚未确认`, {
        draft,
      });
    }
    const validationMessage = showDraftValidation(draft);
    if (validationMessage) {
      draft.confirmed = false;
      draft.editor.details.open = true;
      throw new ReviewValidationError(validationMessage, { draft });
    }
    if (action === "skip") {
      if (level === "university") {
        throw new ReviewValidationError(`学校「${draft.submittedName}」不能归到上级机构`, {
          draft,
        });
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
      throw new ReviewValidationError(`系所「${draft.submittedName}」不能在归到学校后单独建立`, {
        draft,
      });
    }
    if (action === "existing") {
      const organizationId = parseOrganizationInput(
        editor.existingInput,
        editor.allowedExistingIds,
      );
      if (!organizationId) {
        throw new ReviewValidationError(
          `${LEVEL_LABELS[level]}「${draft.submittedName}」需要选择现有机构`,
          { draft },
        );
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
      throw new ReviewValidationError(
        `${LEVEL_LABELS[level]}「${draft.submittedName}」需要填写正式名称`,
        { draft },
      );
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
        throw new ReviewValidationError(`${row.name}需要选择归属机构`, {
          card,
          row,
        });
      }
      const organization = state.selectableOrganizationById.get(organizationId);
      const verificationUrls = [row.source_url].filter(Boolean);
      if (
        !organization ||
        !MentorReviewLogic.hasOfficialEvidence(
          verificationUrls,
          organization.approved_domains || [],
        )
      ) {
        throw new ReviewValidationError(
          `${row.name}的发现来源页不属于调整后机构的官方域名`,
          { card, row },
        );
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
      throw new ReviewValidationError(`${row.name}的逐行处理方式无效`, { card, row });
    }
    const reason = (editor?.reason.value || restored?.reason || "").trim();
    if (!reason) {
      throw new ReviewValidationError(`不收录${row.name}时需要填写原因`, { card, row });
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

function collectIdentityResolutions(card) {
  const resolutions = [];
  for (const row of card.group.rows) {
    if (row.identity?.requires_resolution !== true) {
      continue;
    }
    const editor = state.rowEditorByProposalId.get(row.proposal_id);
    const restored = state.restoredRowValues.get(row.proposal_id);
    const action = editor?.action.value || restored?.action || "follow";
    const targetId = editor ? effectiveRowTargetId(card, editor) : null;
    if (action === "reject" || targetId === null || isCurrentIdentityOrganization(editor, targetId)) {
      continue;
    }
    const resolutionAction = editor?.identityAction.value || restored?.identity_action || "";
    const reason = (editor?.identityReason.value || restored?.identity_reason || "").trim();
    if (!resolutionAction) {
      throw new ReviewValidationError(
        `${row.name}在社区库中已有其他机构任职；请选择双聘、任职调动或不收录这位导师`,
        { card, row },
      );
    }
    if (!reason) {
      throw new ReviewValidationError(`${row.name}的任职判定需要填写依据`, { card, row });
    }
    if (resolutionAction === "append_current_affiliation") {
      resolutions.push({
        proposal_id: row.proposal_id,
        action: resolutionAction,
        make_primary: Boolean(editor?.identityMakePrimary.checked || restored?.identity_make_primary),
        former_affiliation_id: null,
        reason,
      });
      continue;
    }
    if (resolutionAction !== "transfer_current_affiliation") {
      throw new ReviewValidationError(`${row.name}的任职处理方式无效`, { card, row });
    }
    const formerAffiliationId = editor
      ? editor.identityFormerAffiliation.value
      : restored?.identity_former_affiliation_id;
    if (!formerAffiliationId) {
      throw new ReviewValidationError(`${row.name}调动时需要选择原任职`, { card, row });
    }
    resolutions.push({
      proposal_id: row.proposal_id,
      action: resolutionAction,
      make_primary: true,
      former_affiliation_id: formerAffiliationId,
      reason,
    });
  }
  return resolutions;
}

function validateFinalAssignmentSources(decisions) {
  const decisionsByGroup = new Map(decisions.map((decision) => [decision.group_id, decision]));
  for (const card of state.cards) {
    const decision = decisionsByGroup.get(card.group.id);
    const overrides = new Map(
      decision.row_overrides.map((override) => [override.proposal_id, override]),
    );
    for (const row of card.group.rows) {
      const override = overrides.get(row.proposal_id);
      if (override?.action === "reject" || (!override && decision.action === "reject")) {
        continue;
      }
      const targetId =
        override?.action === "map_existing"
          ? override.organization_id
          : decision.target_organization_id || standardGroupTargetId(card.group);
      const organization = state.selectableOrganizationById.get(targetId);
      const verificationUrls = [row.source_url].filter(Boolean);
      if (
        !organization ||
        !MentorReviewLogic.hasOfficialEvidence(
          verificationUrls,
          organization.approved_domains || [],
        )
      ) {
        throw new ReviewValidationError(
          `${row.name}的发现来源页不属于最终归属机构「${
            organization?.canonical_name || targetId || "未选择"
          }」的官方域名`,
          { card, row },
        );
      }
    }
  }
}

function focusReviewValidationError(error) {
  if (!(error instanceof ReviewValidationError) || !error.taskId) {
    return false;
  }
  document
    .querySelectorAll(".has-review-error")
    .forEach((node) => node.classList.remove("has-review-error"));

  if (error.card) {
    error.card.workflowConfirmed = false;
    clearWorkflowCompletion(error.card);
    error.card.taskStatus.textContent = error.message;
    error.card.taskStatus.classList.add("error");
    error.card.taskStatus.hidden = false;
    if (error.focusTarget === "target") {
      error.card.advancedDetails.open = true;
      showIndependentTargetError(error.card, error.message);
    }
  }
  if (error.draft) {
    error.draft.confirmed = false;
    clearWorkflowCompletion(error.draft);
    error.draft.editor.details.open = true;
    error.draft.editor.error.textContent = error.message;
    error.draft.editor.error.hidden = false;
    error.draft.editor.details.classList.add("has-error");
  }

  state.workflowFilter = "pending";
  state.currentWorkflowTaskId = error.taskId;
  refreshWorkflowTasks();
  selectWorkflowTask(error.taskId, { focus: false });

  let target = error.draft?.editor.details || error.card?.article || null;
  if (error.proposalId && error.card) {
    if (error.card.rowsDetails) {
      error.card.rowsDetails.open = true;
    }
    let editor = state.rowEditorByProposalId.get(error.proposalId);
    while (
      !editor &&
      error.card.loadMoreRows &&
      error.card.renderedRowCount < error.card.ordinaryRowCount
    ) {
      error.card.loadMoreRows();
      editor = state.rowEditorByProposalId.get(error.proposalId);
    }
    if (editor) {
      editor.wrapper.classList.add("has-review-error");
      editor.wrapper.tabIndex = -1;
      target = editor.wrapper;
    }
  } else if (error.focusTarget === "target" && error.card) {
    error.card.targetPanel.classList.add("has-review-error");
    target = error.card.targetPanel;
  }
  window.requestAnimationFrame(() => {
    if (target?.isConnected) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.focus?.({ preventScroll: true });
    }
  });
  return true;
}

async function collectDecision() {
  await updateOrganizationDrafts();
  const pendingWorkflowTasks = state.workflowTasks.filter(
    (task) => workflowTaskStatus(task) === "pending",
  );
  if (pendingWorkflowTasks.length) {
    state.workflowFilter = "pending";
    refreshWorkflowTasks();
    selectWorkflowTask(pendingWorkflowTasks[0].id, { focus: false });
    const pendingNodeCount = state.workflowNodes.filter(
      (node) => node.pendingTasks.length > 0,
    ).length;
    throw new Error(`还有 ${pendingNodeCount} 个节点需要处理`);
  }
  const pending = pendingOrganizationDrafts();
  if (pending.length) {
    throw new ReviewValidationError(`还有 ${pending.length} 个机构尚未确认`, {
      draft: pending[0],
    });
  }
  for (const card of state.cards) {
    if (card.mappingMode.value === "standard") {
      continue;
    }
    const error = await updateIndependentTargetCard(card, true);
    if (error) {
      throw new ReviewValidationError(error, { card, focusTarget: "target" });
    }
  }
  const decisions = [];
  const referencedOrganizationIds = new Set();
  for (const card of state.cards) {
    const rowOverrides = collectRowOverrides(card);
    const identityResolutions = collectIdentityResolutions(card);
    for (const override of rowOverrides) {
      if (override.action === "map_existing") {
        referencedOrganizationIds.add(override.organization_id);
      }
    }
    if (card.groupAction.value === "reject") {
      const reason = card.groupReason.value.trim();
      if (!reason) {
        throw new ReviewValidationError(`不收录${pathText(card.group)}时需要填写原因`, {
          card,
        });
      }
      decisions.push({
        group_id: card.group.id,
        action: "reject",
        reason,
        levels: [],
        target_organization_id: null,
        mapping_kind: "standard",
        mapping_reason: null,
        save_path_correction: false,
        row_overrides: rowOverrides,
        identity_resolutions: identityResolutions,
      });
      continue;
    }
    const requiredLevels = requiredSubmittedLevelsForCard(card);
    const levelResolution = await collectLevels(card.group, requiredLevels);
    const corrected = card.mappingMode.value === "corrected";
    const targetId = corrected ? card.independentTargetId : null;
    const targetOrganization = corrected
      ? state.selectableOrganizationById.get(targetId)
      : null;
    if (corrected && !targetOrganization) {
      throw new ReviewValidationError(`“${pathText(card.group)}”还没有选择有效的归属机构`, {
        card,
        focusTarget: "target",
      });
    }
    const overriddenProposalIds = new Set(
      rowOverrides.map((override) => override.proposal_id),
    );
    if (
      corrected &&
      card.group.rows.some((row) => !overriddenProposalIds.has(row.proposal_id))
    ) {
      referencedOrganizationIds.add(targetId);
    }
    decisions.push({
      group_id: card.group.id,
      action: "resolve",
      reason: null,
      levels: levelResolution.levels,
      target_organization_id: targetId,
      mapping_kind: corrected ? mappingKindForTarget(card, targetOrganization) : "standard",
      mapping_reason: corrected ? card.mappingReason.value.trim() : null,
      save_path_correction: corrected ? card.savePathCorrection.checked : false,
      row_overrides: rowOverrides,
      identity_resolutions: identityResolutions,
    });
  }

  const independentCreationCandidates = state.cards
    .filter(
      (card) =>
        card.mappingMode.value !== "standard" &&
        card.targetAction.value === "create" &&
        card.independentCreation,
    )
    .map((card) => card.independentCreation);
  const mergedCreations = MentorReviewLogic.mergeIndependentCreations(
    independentCreationCandidates,
    referencedOrganizationIds,
  );
  if (mergedCreations.unusedIds.length) {
    throw new Error(
      `备用机构尚未分配给任何导师：${mergedCreations.unusedIds.join("，")}`,
    );
  }
  validateFinalAssignmentSources(decisions);
  return {
    schema_version: 1,
    kind: "batch_organization_review_decision",
    pull_request_number: state.pullNumber,
    issue_number: state.issueNumber,
    manifest_sha256: state.manifestSha256,
    organization_creations: mergedCreations.creations,
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
  for (const creation of decision.organization_creations || []) {
    createdIds.add(creation.organization_id);
  }
  let mappedRows = 0;
  let rejectedRows = 0;
  let adjustedRows = 0;
  let correctedRows = 0;
  let ignoredExternalProfiles = 0;
  let dualAppointments = 0;
  let transfers = 0;
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
        const targetId =
          override?.action === "map_existing"
            ? override.organization_id
            : groupDecision.target_organization_id || standardGroupTargetId(card.group);
        const organization = state.selectableOrganizationById.get(targetId);
        if (
          row.profile_url &&
          organization &&
          !MentorReviewLogic.hasOfficialEvidence(
            [row.profile_url],
            organization.approved_domains || [],
          )
        ) {
          ignoredExternalProfiles += 1;
        }
        if (override?.action === "map_existing") {
          adjustedRows += 1;
        } else if (groupDecision.mapping_kind && groupDecision.mapping_kind !== "standard") {
          correctedRows += 1;
        }
      }
    }
    for (const resolution of groupDecision.identity_resolutions || []) {
      if (resolution.action === "append_current_affiliation") {
        dualAppointments += 1;
      } else if (resolution.action === "transfer_current_affiliation") {
        transfers += 1;
      }
    }
  }
  const metrics = [
    ["新建机构", createdIds.size],
    ["导入导师", mappedRows],
    ["调整整组归属", correctedRows],
    ["单独调整导师归属", adjustedRows],
    ["增加双聘任职", dualAppointments],
    ["记录任职调动", transfers],
    ["忽略非官网详情页", ignoredExternalProfiles],
    ["不收录导师", rejectedRows],
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
    const fullBody = `${COMMENT_MARKER}\n\`\`\`json\n${JSON.stringify(decision)}\n\`\`\``;
    const compactDecision = MentorReviewLogic.compactDecisionForComment(decision);
    const compactBody =
      `${COMMENT_MARKER}\n\`\`\`json\n${JSON.stringify(compactDecision)}\n\`\`\``;
    const fullCharacterCount = Array.from(fullBody).length;
    const compactCharacterCount = Array.from(compactBody).length;
    const useCompact = compactCharacterCount < fullCharacterCount;
    const body = useCompact ? compactBody : fullBody;
    const characterCount = useCompact ? compactCharacterCount : fullCharacterCount;
    if (characterCount > GITHUB_COMMENT_CHARACTER_LIMIT) {
      throw new Error(
        `审核评论有 ${characterCount.toLocaleString("zh-CN")} 个字符，超过 GitHub 的 ` +
          `${GITHUB_COMMENT_CHARACTER_LIMIT.toLocaleString("zh-CN")} 字符上限。` +
          "请把投稿拆成较小批次后重试。",
      );
    }
    renderDecisionPreview(decision);
    nodes.decisionText.value = body;
    nodes.decisionOutput.hidden = false;
    const reduction = useCompact
      ? Math.round((1 - compactCharacterCount / fullCharacterCount) * 100)
      : 0;
    nodes.copyStatus.textContent =
      `评论长度 ${characterCount.toLocaleString("zh-CN")} / ` +
      `${GITHUB_COMMENT_CHARACTER_LIMIT.toLocaleString("zh-CN")} 字符` +
      (reduction > 0 ? ` · 已精简 ${reduction}%` : "");
    nodes.decisionOutput.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    const located = focusReviewValidationError(error);
    const message = error instanceof Error ? error.message : "无法生成审核评论";
    nodes.decisionError.textContent = located ? `${message}。已定位到对应记录` : message;
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
  const organizationIds = new Set(
    manifest.organizations
      .map((organization) => organization?.id)
      .filter((organizationId) => typeof organizationId === "string"),
  );
  for (const group of manifest.groups) {
    if (!group || !Array.isArray(group.rows) || typeof group.submitted !== "object") {
      throw new Error("审核清单中没有有效的导师数据组合");
    }
    const correction = group.suggested_path_correction;
    if (correction !== undefined && correction !== null) {
      const targetId = correction.target_organization_id;
      if (
        !["department_as_school", "department_as_institute", "custom"].includes(
          correction.kind,
        ) ||
        !["history", "heuristic"].includes(correction.source) ||
        typeof correction.reason !== "string" ||
        !correction.reason.trim() ||
        correction.reason.length > 500 ||
        (targetId !== null &&
          (typeof targetId !== "string" || !organizationIds.has(targetId)))
      ) {
        throw new Error("审核清单中的机构归属建议格式无效");
      }
    }
    for (const row of group.rows) {
      if (!Object.hasOwn(row || {}, "identity")) {
        continue;
      }
      const identity = row.identity;
      const mentor = identity?.mentor;
      if (
        identity?.requires_resolution !== true ||
        identity?.match_status !== "conflict" ||
        typeof identity?.target_mentor_id !== "string" ||
        !Array.isArray(identity?.review_reasons) ||
        !mentor ||
        typeof mentor.id !== "string" ||
        typeof mentor.name !== "string" ||
        typeof mentor.email !== "string" ||
        !Array.isArray(mentor.affiliations) ||
        mentor.affiliations.length === 0 ||
        !mentor.affiliations.every((affiliation) => {
          const hasResolvedOrganization = typeof affiliation?.organization_id === "string";
          const hasPendingOrganization =
            affiliation?.organization_id === null &&
            typeof affiliation.organization_label === "string" &&
            affiliation.organization_label.trim().length > 0 &&
            affiliation.organization_label.length <= 800;
          return (
            affiliation?.status === "current" &&
            typeof affiliation.id === "string" &&
            (hasResolvedOrganization || hasPendingOrganization) &&
            typeof affiliation.source_url === "string"
          );
        })
      ) {
        throw new Error("审核清单中的导师身份判定信息无效");
      }
    }
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
    const identityCount = manifest.groups.reduce(
      (total, group) =>
        total + group.rows.filter((row) => row.identity?.requires_resolution === true).length,
      0,
    );
    nodes.issue.textContent = `#${issueNumber}`;
    nodes.groupCount.textContent = String(manifest.groups.length);
    nodes.rowCount.textContent = String(rowCount);
    nodes.invalidCount.textContent = String(manifest.invalid_rows.length);
    nodes.identityCount.textContent = String(identityCount);
    nodes.invalidSummary.hidden = manifest.invalid_rows.length === 0;
    nodes.identitySummary.hidden = identityCount === 0;
    nodes.summary.hidden = false;
    renderInvalidRows();

    if (manifest.groups.length) {
      const drafts = buildOrganizationDrafts();
      renderOrganizationDraftTree(drafts);
      nodes.reviewWorkspace.hidden = false;
      for (const group of manifest.groups) {
        const card = createGroupCard(group);
        state.cards.push(card);
        state.groupCardById.set(group.id, card);
      }
      const restored = restoreReviewDraft();
      await updateOrganizationDrafts();
      if (!restored && applyInitialPathSuggestions()) {
        await updateOrganizationDrafts();
      }
      const pending = pendingOrganizationDrafts();
      for (const draft of state.organizationDrafts) {
        if (!draftIsActive(draft) || draft.confirmed) {
          draft.editor.details.open = false;
        }
      }
      for (const [index, draft] of pending.entries()) {
        draft.editor.details.open = restored ? draft.editor.details.open : index === 0;
      }
    } else {
      nodes.emptyReview.hidden = false;
    }
    nodes.decisionPanel.hidden = false;
    setStatus("ready", `已读取 PR #${pullNumber}`, "");
  } catch (error) {
    setStatus("error", "无法打开审核清单", error instanceof Error ? error.message : "未知错误");
  }
}

nodes.generate.addEventListener("click", () => void generateDecision());
nodes.copyOpen.addEventListener("click", () => void copyAndOpenPullRequest());
nodes.taskFilterPending.addEventListener("click", () => setWorkflowFilter("pending"));
nodes.taskFilterDone.addEventListener("click", () => setWorkflowFilter("done"));
nodes.taskFilterAll.addEventListener("click", () => setWorkflowFilter("all"));
nodes.treeSearch.addEventListener("input", () => {
  state.workflowSearchQuery = nodes.treeSearch.value.trim();
  refreshWorkflowTasks();
});
nodes.previousTask.addEventListener("click", () => moveWorkflowTask(-1));
nodes.confirmTask.addEventListener("click", () => void confirmCurrentWorkflowTask());
nodes.nextTask.addEventListener("click", () => moveWorkflowTask(1));
window.addEventListener("pagehide", saveReviewDraft);
void loadReview();
