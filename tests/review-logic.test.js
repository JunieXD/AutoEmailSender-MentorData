"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const context = {};
context.globalThis = context;
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, "..", "site", "review-logic.js"), "utf8"),
  context,
);
const logic = context.MentorReviewLogic;

test("学院和研究院后缀会进入人工判断但不会默认新建", () => {
  const school = logic.correctionDefaults(
    { school: "计算机学院", department: "人工智能学院" },
    null,
  );
  const institute = logic.correctionDefaults(
    { school: "计算机学院", department: "人工智能研究院" },
    null,
  );

  assert.equal(school.kind, "department_as_school");
  assert.equal(school.organizationType, "school");
  assert.equal(school.mode, "standard");
  assert.equal(school.targetAction, "existing");
  assert.equal(school.needsReview, true);
  assert.equal(school.savePathCorrection, true);
  assert.equal(institute.kind, "department_as_institute");
  assert.equal(institute.organizationType, "institute");
  assert.equal(
    logic.correctionDefaults(
      { school: " 计算机学院 ", department: "计算机学院" },
      null,
    ),
    null,
  );
});

test("历史纠错直接默认选中已有目标", () => {
  const defaults = logic.correctionDefaults(
    { school: "计算机学院", department: "人工智能学院" },
    {
      kind: "department_as_school",
      target_organization_id: "org_ai_school",
      source: "history",
      reason: "已由上一批审核确认",
    },
  );

  assert.equal(defaults.targetAction, "existing");
  assert.equal(defaults.mode, "corrected");
  assert.equal(defaults.targetId, "org_ai_school");
  assert.equal(defaults.source, "history");
  assert.equal(defaults.reason, "已由上一批审核确认");
});

test("路径建议识别重复上级和带前缀的学院写法", () => {
  const exact = logic.pathReviewSuggestion({
    university: "示例大学",
    school: "智能科学与技术学院",
    department: "智能科学与技术学院",
  });
  const prefixed = logic.pathReviewSuggestion({
    university: "国科大杭州高等研究院",
    school: "智能科学与技术学院",
    department: "杭高院-智能科学与技术学院",
  });
  const longPrefixed = logic.pathReviewSuggestion({
    university: "国科大杭州高等研究院",
    school: "智能科学与技术学院",
    department: "国科大杭州高等研究院智能科学与技术学院",
  });

  assert.equal(exact.kind, "same_as_parent");
  assert.equal(exact.action, "use_parent");
  assert.equal(exact.confidence, "certain");
  assert.equal(prefixed.kind, "parent_name_with_prefix");
  assert.equal(prefixed.action, "use_parent");
  assert.equal(prefixed.confidence, "high");
  assert.equal(prefixed.reason, "名称可能只是当前学院的另一种写法。");
  assert.equal(longPrefixed.kind, "parent_name_with_prefix");
});

test("历史审核结果优先于名称推断", () => {
  const suggestion = logic.pathReviewSuggestion(
    {
      university: "示例大学",
      school: "智能科学与技术学院",
      department: "示例校区-智能科学与技术学院",
    },
    {
      kind: "custom",
      target_organization_id: "org_reviewed_destination",
      source: "history",
      reason: "上一批已经核对官网并确认归属",
    },
  );

  assert.equal(suggestion.kind, "known_destination");
  assert.equal(suggestion.action, "use_existing");
  assert.equal(suggestion.targetId, "org_reviewed_destination");
  assert.equal(suggestion.confidence, "certain");
});

test("只有机构后缀时要求判断层级，不替审核者做决定", () => {
  const suggestion = logic.pathReviewSuggestion(
    {
      university: "示例大学",
      school: "计算机学院",
      department: "人工智能学院",
    },
    {
      kind: "department_as_school",
      target_organization_id: null,
      source: "heuristic",
      reason: "名称看起来像学院",
    },
  );

  assert.equal(suggestion.kind, "ambiguous_hierarchy");
  assert.equal(suggestion.action, "review_hierarchy");
  assert.equal(suggestion.confidence, "review");

  const researchInstitute = logic.pathReviewSuggestion({
    university: "示例大学",
    school: "计算机学院",
    department: "先进计算研究所",
  });
  assert.equal(researchInstitute.kind, "ambiguous_hierarchy");
  assert.equal(researchInstitute.action, "review_hierarchy");
});

test("候选机构优先显示名称、当前批次、同校和同域名相关项", () => {
  const context = {
    submitted: {
      university: "示例大学",
      school: "智能学院",
      department: "智能实验室",
    },
    source_domains: ["ai.example.edu"],
  };
  const unrelated = {
    id: "org_unrelated",
    canonical_name: "其他学院",
    aliases: [],
    lineage_names: ["其他大学", "其他学院"],
    approved_domains: [],
  };
  const sameUniversity = {
    id: "org_same_university",
    canonical_name: "计算机学院",
    aliases: [],
    lineage_names: ["示例大学", "计算机学院"],
    approved_domains: [],
  };
  const pending = {
    id: "org_pending",
    canonical_name: "本批新机构",
    aliases: [],
    lineage_names: ["示例大学", "本批新机构"],
    approved_domains: [],
    pending: true,
  };
  const unrelatedPending = {
    id: "org_unrelated_pending",
    canonical_name: "外校本批新机构",
    aliases: [],
    lineage_names: ["外校", "外校本批新机构"],
    approved_domains: [],
    pending: true,
  };
  const exact = {
    id: "org_exact",
    canonical_name: "人工智能实验室",
    aliases: ["智能实验室"],
    lineage_names: ["示例大学", "智能学院", "人工智能实验室"],
    approved_domains: ["ai.example.edu"],
  };

  const ranked = logic.rankOrganizationCandidates(context, [
    unrelated,
    sameUniversity,
    unrelatedPending,
    pending,
    exact,
  ]);
  assert.deepEqual(
    Array.from(ranked, (organization) => organization.id),
    [
      "org_exact",
      "org_pending",
      "org_same_university",
      "org_unrelated_pending",
      "org_unrelated",
    ],
  );
});

test("整组纠错只激活新目标所需的原路径上级", () => {
  const required = (overrides) =>
    Array.from(
      logic.requiredSubmittedLevels({
        mappingMode: "corrected",
        targetAction: "create",
        parentMode: "group",
        organizationType: "school",
        ...overrides,
      }),
    );

  assert.deepEqual(required({ mappingMode: "standard" }), [
    "university",
    "school",
    "department",
  ]);
  assert.deepEqual(required({ mappingMode: "alternate" }), [
    "university",
    "school",
    "department",
  ]);
  assert.deepEqual(required({}), ["university"]);
  assert.deepEqual(required({ organizationType: "laboratory" }), ["university", "school"]);
  assert.deepEqual(required({ targetAction: "existing" }), []);
  assert.deepEqual(required({ parentMode: "other" }), []);
});

test("独立新建机构去重合并域名并报告未使用项", () => {
  const base = {
    organization_id: "org_auto_example",
    organization_type: "school",
    canonical_name: "人工智能学院",
    parent_id: "org_university",
    official_url: null,
    approved_domains: ["ai.example.edu"],
  };
  const merged = logic.mergeIndependentCreations(
    [base, { ...base, approved_domains: ["faculty.example.edu"] }],
    new Set([base.organization_id]),
  );

  assert.equal(merged.creations.length, 1);
  assert.deepEqual(Array.from(merged.creations[0].approved_domains), [
    "ai.example.edu",
    "faculty.example.edu",
  ]);
  assert.deepEqual(Array.from(merged.unusedIds), []);

  const unused = logic.mergeIndependentCreations([base], new Set());
  assert.deepEqual(Array.from(unused.unusedIds), [base.organization_id]);
});
