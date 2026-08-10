const REPOSITORY = "JunieXD/AutoEmailSender-MentorData";
const COMMENT_MARKER = "<!-- mentor-data-report-review:v1 -->";
const BRANCH_PATTERN = /^report\/issue-([1-9][0-9]*)$/;
const SHA_PATTERN = /^[a-f0-9]{40,64}$/;
const MENTOR_PATTERN = /^mentor_[a-z0-9][a-z0-9_-]{7,63}$/;
const ORGANIZATION_PATTERN = /^org_[a-z0-9][a-z0-9_-]{2,63}$/;
const MAX_PROPOSAL_BYTES = 1_000_000;
const GITHUB_COMMENT_CHARACTER_LIMIT = 65_536;

const state = {
  pullNumber: null,
  pullUrl: null,
  issueNumber: null,
  proposal: null,
  proposalSha256: null,
  storageKey: null,
};

const nodes = {
  status: document.querySelector("#report-review-status"),
  statusTitle: document.querySelector("#report-status-title"),
  statusDetail: document.querySelector("#report-status-detail"),
  summary: document.querySelector("#report-summary"),
  issue: document.querySelector("#report-issue"),
  mentor: document.querySelector("#report-mentor"),
  fields: document.querySelector("#report-fields"),
  evidenceCount: document.querySelector("#report-evidence-count"),
  comparison: document.querySelector("#report-comparison"),
  reportType: document.querySelector("#report-type"),
  issueLink: document.querySelector("#source-issue-link"),
  proposedValue: document.querySelector("#proposed-value"),
  proposedExplanation: document.querySelector("#proposed-explanation"),
  evidenceLinks: document.querySelector("#evidence-links"),
  currentName: document.querySelector("#current-name"),
  currentStatus: document.querySelector("#current-status"),
  currentRecord: document.querySelector("#current-record"),
  decisionPanel: document.querySelector("#report-decision-panel"),
  decisionInputs: [...document.querySelectorAll('input[name="report-decision"]')],
  reason: document.querySelector("#moderator-reason"),
  reasonHint: document.querySelector("#reason-hint"),
  autosave: document.querySelector("#report-autosave"),
  acceptedEditor: document.querySelector("#accepted-editor"),
  selectedChangeCount: document.querySelector("#selected-change-count"),
  generate: document.querySelector("#generate-report-decision"),
  error: document.querySelector("#report-decision-error"),
  preview: document.querySelector("#report-decision-preview"),
  output: document.querySelector("#report-decision-output"),
  outputText: document.querySelector("#report-decision-text"),
  copyOpen: document.querySelector("#copy-open-report-pr"),
  copyStatus: document.querySelector("#report-copy-status"),
};

const fields = {
  contacts: {
    checkbox: document.querySelector("#change-email"),
    summary: document.querySelector("#email-summary"),
    action: document.querySelector("#email-action"),
    value: document.querySelector("#accepted-email"),
    source: document.querySelector("#email-source"),
  },
  names: {
    checkbox: document.querySelector("#change-name"),
    summary: document.querySelector("#name-summary"),
    value: document.querySelector("#accepted-name"),
  },
  title: {
    checkbox: document.querySelector("#change-title"),
    summary: document.querySelector("#title-summary"),
    value: document.querySelector("#accepted-title"),
  },
  profiles: {
    checkbox: document.querySelector("#change-profile"),
    summary: document.querySelector("#profile-summary"),
    action: document.querySelector("#profile-action"),
    value: document.querySelector("#accepted-profile"),
    source: document.querySelector("#profile-source"),
  },
  affiliations: {
    checkbox: document.querySelector("#change-affiliation"),
    summary: document.querySelector("#affiliation-summary"),
    organization: document.querySelector("#accepted-organization"),
    title: document.querySelector("#accepted-affiliation-title"),
    source: document.querySelector("#affiliation-source"),
  },
  status: {
    checkbox: document.querySelector("#change-status"),
    summary: document.querySelector("#status-summary"),
    value: document.querySelector("#accepted-status"),
    reason: document.querySelector("#status-reason"),
    source: document.querySelector("#status-source"),
    observed: document.querySelector("#status-observed"),
  },
  research_directions: {
    checkbox: document.querySelector("#change-research"),
    summary: document.querySelector("#research-summary"),
    value: document.querySelector("#accepted-research"),
  },
  recent_papers: {
    checkbox: document.querySelector("#change-papers"),
    summary: document.querySelector("#papers-summary"),
    value: document.querySelector("#accepted-papers"),
  },
};

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

async function sha256Hex(value) {
  const bytes = value instanceof ArrayBuffer ? value : new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function isWebUrl(value) {
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function safeText(value, fallback = "未提供") {
  const text = String(value || "").trim();
  return text || fallback;
}

function formatStatus(value) {
  return {
    active: "在职",
    retired: "已退休",
    departed: "已离职",
    deceased: "已去世",
    stale: "长期未核验",
    disputed: "存在争议",
    removed: "已移除",
  }[value] || value || "未知";
}

function currentPrimary(collection) {
  return (collection || []).find((item) => item.is_primary && item.status === "current") ||
    (collection || []).find((item) => item.is_primary) ||
    (collection || [])[0] || null;
}

function appendRecordRow(label, value, options = {}) {
  const wrapper = element("div", options.wide ? "wide-record-row" : null);
  const term = element("dt", null, label);
  const detail = document.createElement("dd");
  if (Array.isArray(value)) {
    if (!value.length) {
      detail.textContent = "未填写";
    } else {
      const list = element("ul", "record-list");
      for (const item of value) {
        list.append(element("li", null, safeText(item)));
      }
      detail.append(list);
    }
  } else if (options.url && isWebUrl(value)) {
    const link = element("a", null, value);
    link.href = value;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    detail.append(link);
  } else {
    detail.textContent = safeText(value);
  }
  wrapper.append(term, detail);
  nodes.currentRecord.append(wrapper);
}

function renderCurrentRecord() {
  const before = state.proposal.before;
  nodes.currentRecord.replaceChildren();
  appendRecordRow("主邮箱", currentPrimary(before.contacts)?.value);
  appendRecordRow("职称", before.title);
  appendRecordRow("导师主页", (before.profiles || []).find((item) => item.status === "current")?.url, {
    url: true,
  });
  appendRecordRow("机构 ID", before.organization_id);
  appendRecordRow("其他邮箱", (before.contacts || [])
    .filter((item) => item !== currentPrimary(before.contacts))
    .map((item) => `${item.value}（${item.status}）`));
  appendRecordRow("研究方向", before.research_directions || [], { wide: true });
}

function renderEvidence() {
  nodes.evidenceLinks.replaceChildren();
  for (const [index, url] of state.proposal.evidence_urls.entries()) {
    const link = element("a", "evidence-link");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.append(
      element("span", "evidence-index", String(index + 1)),
      element("span", "evidence-url", url),
      element("span", "evidence-open", "打开 ↗"),
    );
    nodes.evidenceLinks.append(link);
  }
}

function datetimeLocal(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function setInitialEditorValues() {
  const before = state.proposal.before;
  const evidence = state.proposal.evidence_urls[0] || "";
  const primaryContact = currentPrimary(before.contacts);
  const primaryName = (before.names || []).find((item) => item.is_primary);
  const currentProfile = (before.profiles || []).find((item) => item.status === "current");
  const primaryAffiliation = (before.affiliations || []).find((item) => item.is_primary);
  fields.contacts.value.value = MentorReportReviewLogic.suggestedEmail(state.proposal);
  fields.contacts.source.value = evidence;
  fields.names.value.value = primaryName?.value || before.name || "";
  fields.title.value.value = before.title || primaryAffiliation?.title || "";
  fields.profiles.value.value = currentProfile?.url || "";
  fields.profiles.source.value = evidence;
  fields.affiliations.organization.value = primaryAffiliation?.organization_id || before.organization_id || "";
  fields.affiliations.title.value = primaryAffiliation?.title || before.title || "";
  fields.affiliations.source.value = evidence;
  fields.status.value.value = before.status || "active";
  fields.status.reason.value = before.status_reason || "";
  fields.status.source.value = evidence;
  fields.status.observed.value = datetimeLocal(state.proposal.created_at);
  fields.research_directions.value.value = (before.research_directions || []).join("\n");
  fields.recent_papers.value.value = (before.recent_papers || []).join("\n");
  fields.contacts.summary.textContent = primaryContact
    ? `当前：${primaryContact.value}`
    : "当前无邮箱";
  fields.names.summary.textContent = `当前：${primaryName?.value || before.name || "未填写"}`;
  fields.title.summary.textContent = `当前：${before.title || "未填写"}`;
  fields.profiles.summary.textContent = currentProfile ? "当前有主页" : "当前无主页";
  fields.affiliations.summary.textContent = `当前：${primaryAffiliation?.organization_id || "未填写"}`;
  fields.status.summary.textContent = `当前：${formatStatus(before.status)}`;
  fields.research_directions.summary.textContent = `当前 ${(before.research_directions || []).length} 项`;
  fields.recent_papers.summary.textContent = `当前 ${(before.recent_papers || []).length} 篇`;
}

function selectedDecision() {
  return nodes.decisionInputs.find((input) => input.checked)?.value || "";
}

function updateDecisionState() {
  const decision = selectedDecision();
  const accepted = ["accepted", "partially_accepted"].includes(decision);
  nodes.acceptedEditor.hidden = !accepted;
  nodes.reasonHint.textContent = {
    accepted: "说明证据如何支持最终采用值。这段文字会进入永久审核记录。",
    partially_accepted: "说明接受了哪些内容，以及哪些内容未被证据支持。",
    rejected: "说明官网为什么不能证明原记录错误。",
    needs_evidence: "写明还缺哪一项可核验的高校官网证据。",
    duplicate: "填写已有 Issue、PR 或 Resolution 编号。",
  }[decision] || "这段文字会进入永久审核记录。";
  for (const input of nodes.decisionInputs) {
    input.closest(".decision-choice").classList.toggle("is-selected", input.checked);
  }
  saveDraft();
}

function updateSelectedChanges() {
  const selected = Object.values(fields).filter((field) => field.checkbox.checked).length;
  nodes.selectedChangeCount.textContent = `已选择 ${selected} 项`;
  for (const [name, field] of Object.entries(fields)) {
    const details = document.querySelector(`.change-editor[data-field="${name}"]`);
    details.classList.toggle("is-selected", field.checkbox.checked);
  }
  saveDraft();
}

function inputValues() {
  return [...document.querySelectorAll(
    "#report-decision-panel input, #report-decision-panel select, #report-decision-panel textarea",
  )];
}

function saveDraft() {
  if (!state.storageKey) {
    return;
  }
  const values = {};
  for (const input of inputValues()) {
    if (!input.id && input.name !== "report-decision") {
      continue;
    }
    const key = input.id || `${input.name}:${input.value}`;
    values[key] = input.type === "checkbox" || input.type === "radio" ? input.checked : input.value;
  }
  try {
    localStorage.setItem(state.storageKey, JSON.stringify(values));
    nodes.autosave.textContent = "已自动保存";
  } catch {
    nodes.autosave.textContent = "浏览器未允许保存草稿";
  }
}

function restoreDraft() {
  if (!state.storageKey) {
    return;
  }
  let values;
  try {
    values = JSON.parse(localStorage.getItem(state.storageKey) || "null");
  } catch {
    return;
  }
  if (!values || typeof values !== "object") {
    return;
  }
  for (const input of inputValues()) {
    const key = input.id || `${input.name}:${input.value}`;
    if (!Object.hasOwn(values, key)) {
      continue;
    }
    if (input.type === "checkbox" || input.type === "radio") {
      input.checked = Boolean(values[key]);
    } else if (typeof values[key] === "string") {
      input.value = values[key].slice(0, input.maxLength > 0 ? input.maxLength : 100_000);
    }
  }
  nodes.autosave.textContent = "已恢复本机草稿";
}

function requireWebUrl(value, label) {
  const normalized = String(value || "").trim();
  if (!isWebUrl(normalized)) {
    throw new Error(`${label}必须是有效的 HTTP 或 HTTPS 地址`);
  }
  return normalized;
}

function collectAcceptedPatch() {
  const before = state.proposal.before;
  const accepted = {};
  const observed = state.proposal.created_at;
  if (fields.contacts.checkbox.checked) {
    const email = fields.contacts.value.value.trim().toLocaleLowerCase();
    if (!MentorReportReviewLogic.extractEmails(email).includes(email)) {
      throw new Error("请填写有效的新邮箱");
    }
    accepted.contacts = MentorReportReviewLogic.buildContacts(before, {
      action: fields.contacts.action.value,
      email,
      sourceUrl: requireWebUrl(fields.contacts.source.value, "邮箱证据页面"),
      observedAt: observed,
    });
  }
  if (fields.names.checkbox.checked) {
    const value = fields.names.value.value.trim();
    if (!value) {
      throw new Error("请填写最终主姓名");
    }
    accepted.names = MentorReportReviewLogic.buildNames(before, value);
  }
  if (fields.title.checkbox.checked) {
    const value = fields.title.value.value.trim();
    if (!value) {
      throw new Error("请填写最终职称");
    }
    accepted.title = value;
  }
  if (fields.profiles.checkbox.checked) {
    const url = requireWebUrl(fields.profiles.value.value, "新导师主页");
    requireWebUrl(fields.profiles.source.value, "主页证据页面");
    accepted.profiles = MentorReportReviewLogic.buildProfiles(before, {
      action: fields.profiles.action.value,
      url,
      observedAt: observed,
    });
  }
  if (fields.affiliations.checkbox.checked) {
    const organizationId = fields.affiliations.organization.value.trim();
    if (!ORGANIZATION_PATTERN.test(organizationId)) {
      throw new Error("最终机构 ID 格式无效");
    }
    accepted.affiliations = MentorReportReviewLogic.buildAffiliations(before, {
      organizationId,
      title: fields.affiliations.title.value,
      sourceUrl: requireWebUrl(fields.affiliations.source.value, "任职官网依据"),
      observedAt: observed,
    });
  }
  if (fields.status.checkbox.checked) {
    const reason = fields.status.reason.value.trim();
    if (!reason) {
      throw new Error("修改导师状态时必须填写状态说明");
    }
    accepted.status = fields.status.value.value;
    accepted.status_reason = reason;
    accepted.status_source_url = requireWebUrl(fields.status.source.value, "状态官网依据");
    accepted.status_observed_at = MentorReportReviewLogic.observedAt(fields.status.observed.value);
  }
  if (fields.research_directions.checkbox.checked) {
    accepted.research_directions = MentorReportReviewLogic.uniqueLines(
      fields.research_directions.value.value,
    );
  }
  if (fields.recent_papers.checkbox.checked) {
    const papers = MentorReportReviewLogic.uniqueLines(fields.recent_papers.value.value);
    if (papers.length > 8) {
      throw new Error("代表论文最多保留 8 篇");
    }
    accepted.recent_papers = papers;
  }
  if (!Object.keys(accepted).length) {
    throw new Error("接受或部分接受反馈时，至少勾选一个实际修改字段");
  }
  return accepted;
}

function collectDecision() {
  const decision = selectedDecision();
  if (!decision) {
    throw new Error("请选择审核结论");
  }
  const moderatorReason = nodes.reason.value.trim();
  if (!moderatorReason) {
    throw new Error("请填写审核依据");
  }
  const accepted = ["accepted", "partially_accepted"].includes(decision)
    ? collectAcceptedPatch()
    : {};
  return {
    schema_version: 1,
    kind: "report_review_decision",
    pull_request_number: state.pullNumber,
    issue_number: state.issueNumber,
    proposal_sha256: state.proposalSha256,
    decision,
    moderator_reason: moderatorReason,
    accepted,
  };
}

function decisionLabel(value) {
  return {
    accepted: "接受",
    partially_accepted: "部分接受",
    rejected: "拒绝",
    needs_evidence: "需要补证",
    duplicate: "重复反馈",
  }[value] || value;
}

function renderDecisionPreview(decision) {
  nodes.preview.replaceChildren();
  const summary = element("div", "report-preview-summary");
  summary.append(
    element("span", null, "最终裁决"),
    element("strong", null, decisionLabel(decision.decision)),
  );
  const changes = Object.keys(decision.accepted);
  const changed = element("div", "report-preview-summary");
  changed.append(
    element("span", null, "修改字段"),
    element("strong", null, changes.length ? changes.join("、") : "不修改导师数据"),
  );
  nodes.preview.append(summary, changed);
  nodes.preview.hidden = false;
}

function generateDecision() {
  nodes.error.hidden = true;
  nodes.output.hidden = true;
  nodes.copyStatus.textContent = "";
  try {
    const decision = collectDecision();
    const body = `${COMMENT_MARKER}\n\`\`\`json\n${JSON.stringify(decision)}\n\`\`\``;
    const characterCount = Array.from(body).length;
    if (characterCount > GITHUB_COMMENT_CHARACTER_LIMIT) {
      throw new Error("审核评论超过 GitHub 字符上限，请减少论文或研究方向内容");
    }
    renderDecisionPreview(decision);
    nodes.outputText.value = body;
    nodes.output.hidden = false;
    nodes.copyStatus.textContent =
      `评论长度 ${characterCount.toLocaleString("zh-CN")} / ` +
      `${GITHUB_COMMENT_CHARACTER_LIMIT.toLocaleString("zh-CN")} 字符，可以安全提交。`;
    nodes.output.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    nodes.error.textContent = error instanceof Error ? error.message : "无法生成审核评论";
    nodes.error.hidden = false;
  }
}

async function copyAndOpenPullRequest() {
  const pullWindow = window.open(state.pullUrl, "_blank", "noopener,noreferrer");
  let copied = false;
  try {
    await navigator.clipboard.writeText(nodes.outputText.value);
    copied = true;
  } catch {
    nodes.outputText.focus();
    nodes.outputText.select();
    copied = document.execCommand("copy");
  }
  nodes.copyStatus.textContent = copied
    ? "已复制。请在刚打开的 PR 中粘贴并发表评论，之后无需手动操作。"
    : "浏览器未允许自动复制，请手动复制文本框内容。";
  if (!pullWindow) {
    nodes.copyStatus.textContent += " 浏览器拦截了新窗口，请手动打开 PR。";
  }
}

function validateProposal(proposal, issueNumber) {
  if (
    !proposal ||
    proposal.schema_version !== 1 ||
    proposal.kind !== "correction_report" ||
    proposal.issue?.number !== issueNumber ||
    !MENTOR_PATTERN.test(proposal.mentor_id || "") ||
    proposal.decision !== "pending" ||
    Object.keys(proposal.accepted || {}).length !== 0 ||
    !proposal.before ||
    typeof proposal.before !== "object" ||
    !proposal.proposed ||
    typeof proposal.proposed !== "object" ||
    !Array.isArray(proposal.evidence_urls) ||
    !proposal.evidence_urls.length ||
    !proposal.evidence_urls.every(isWebUrl)
  ) {
    throw new Error("PR 中的信息反馈提案格式或 Issue 归属不正确");
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
      throw new Error("该 PR 不是开放的内部信息反馈分支");
    }
    const issueNumber = Number(branchMatch[1]);
    const proposalUrl =
      `https://raw.githubusercontent.com/${REPOSITORY}/${pull.head.sha}` +
      `/reports/pending/issue-${issueNumber}.json`;
    const proposalResponse = await fetch(proposalUrl, { cache: "no-store" });
    if (!proposalResponse.ok) {
      throw new Error(`无法读取反馈提案（GitHub 返回 ${proposalResponse.status}）`);
    }
    const proposalBuffer = await proposalResponse.arrayBuffer();
    if (proposalBuffer.byteLength === 0 || proposalBuffer.byteLength > MAX_PROPOSAL_BYTES) {
      throw new Error("反馈提案为空或超过页面处理上限");
    }
    const proposal = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(proposalBuffer),
    );
    validateProposal(proposal, issueNumber);

    state.pullNumber = pullNumber;
    state.pullUrl = `https://github.com/${REPOSITORY}/pull/${pullNumber}`;
    state.issueNumber = issueNumber;
    state.proposal = proposal;
    state.proposalSha256 = await sha256Hex(proposalBuffer);
    state.storageKey = `mentor-data-report-review:${pullNumber}:${state.proposalSha256}`;

    nodes.issue.textContent = `#${issueNumber}`;
    nodes.mentor.textContent = proposal.before.name || proposal.mentor_id;
    nodes.fields.textContent = proposal.affected_fields;
    nodes.evidenceCount.textContent = String(proposal.evidence_urls.length);
    nodes.reportType.textContent = proposal.report_type;
    nodes.issueLink.href = proposal.issue.url;
    nodes.proposedValue.textContent = safeText(proposal.proposed.value);
    nodes.proposedExplanation.textContent = safeText(proposal.proposed.explanation);
    nodes.currentName.textContent = proposal.before.name || proposal.mentor_id;
    nodes.currentStatus.textContent = formatStatus(proposal.before.status);
    nodes.currentStatus.dataset.status = proposal.before.status || "unknown";
    renderEvidence();
    renderCurrentRecord();
    setInitialEditorValues();
    restoreDraft();
    updateDecisionState();
    updateSelectedChanges();
    nodes.summary.hidden = false;
    nodes.comparison.hidden = false;
    nodes.decisionPanel.hidden = false;
    setStatus(
      "ready",
      "反馈提案已验证",
      `审核结果会绑定 PR #${pullNumber} 的当前提案快照，提交前仍会由后端复核。`,
    );
  } catch (error) {
    setStatus("error", "无法打开信息反馈审核", error instanceof Error ? error.message : "未知错误");
  }
}

for (const input of nodes.decisionInputs) {
  input.addEventListener("change", updateDecisionState);
}
for (const field of Object.values(fields)) {
  field.checkbox.addEventListener("change", () => {
    const details = field.checkbox.closest("details");
    if (field.checkbox.checked) {
      details.open = true;
    }
    updateSelectedChanges();
  });
}
for (const input of inputValues()) {
  if (input.type !== "radio" && input.type !== "checkbox") {
    input.addEventListener(input.tagName === "SELECT" ? "change" : "input", saveDraft);
  }
}
nodes.generate.addEventListener("click", generateDecision);
nodes.copyOpen.addEventListener("click", () => void copyAndOpenPullRequest());
window.addEventListener("pagehide", saveDraft);
void loadReview();
