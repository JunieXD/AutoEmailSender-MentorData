"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const context = {};
context.globalThis = context;
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, "..", "site", "report-review-logic.js"), "utf8"),
  context,
);
const logic = context.MentorReportReviewLogic;

const before = {
  affiliations: [
    {
      id: "aff_fixture_primary",
      organization_id: "org_example_cs",
      status: "current",
      is_primary: true,
      title: "教授",
      started_at: null,
      ended_at: null,
      source_url: "https://cs.example.edu/faculty/mentor",
      observed_at: "2026-08-01T00:00:00Z",
    },
  ],
  contacts: [
    {
      value: "old@example.edu",
      status: "current",
      is_primary: true,
      affiliation_id: "aff_fixture_primary",
      source_url: "https://cs.example.edu/faculty/mentor",
      observed_at: "2026-08-01T00:00:00Z",
    },
  ],
  names: [{ value: "示例导师", kind: "native", is_primary: true }],
  profiles: [],
};

test("从反馈文本中提取不同于当前值的候选邮箱", () => {
  const proposal = {
    before,
    proposed: {
      value: "正确邮箱是 New@Example.edu",
      explanation: "请替换 old@example.edu",
    },
  };
  assert.equal(logic.suggestedEmail(proposal), "new@example.edu");
});

test("替换主邮箱时保留旧邮箱为历史记录", () => {
  const contacts = logic.buildContacts(before, {
    action: "replace_primary",
    email: "new@example.edu",
    sourceUrl: "https://cs.example.edu/faculty/mentor",
    observedAt: "2026-08-10T00:00:00Z",
  });

  assert.equal(contacts.length, 2);
  assert.equal(contacts[0].status, "former");
  assert.equal(contacts[0].is_primary, false);
  assert.equal(contacts[1].value, "new@example.edu");
  assert.equal(contacts[1].is_primary, true);
  assert.equal(contacts[1].affiliation_id, "aff_fixture_primary");
});

test("研究方向和论文按行去空并去重", () => {
  assert.deepEqual(Array.from(logic.uniqueLines("器件建模\n\n器件建模\n射频电路")), [
    "器件建模",
    "射频电路",
  ]);
});
