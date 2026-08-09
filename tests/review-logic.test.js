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

test("学院和研究院后缀会生成平级机构纠错默认值", () => {
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
  assert.equal(school.targetAction, "create");
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
  assert.equal(defaults.targetId, "org_ai_school");
  assert.equal(defaults.source, "history");
  assert.equal(defaults.reason, "已由上一批审核确认");
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
