"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const context = {};
context.globalThis = context;
context.URL = URL;
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, "..", "site", "review-logic.js"), "utf8"),
  context,
);
const logic = context.MentorReviewLogic;

test("学院和研究院后缀会进入人工判断，研究所默认保留为学院下级", () => {
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
      { school: "计算机学院", department: "先进计算研究所" },
      {
        kind: "department_as_institute",
        target_organization_id: null,
        source: "heuristic",
        reason: "旧版审核建议",
      },
    ),
    null,
  );
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
  assert.equal(prefixed.reason, "名称与当前学院相同或只是写法不同。");
  assert.equal(longPrefixed.kind, "parent_name_with_prefix");
});

test("混合多个明确机构时不会因末尾名称相同而归入上级", () => {
  const suggestion = logic.pathReviewSuggestion({
    university: "国科大杭州高等研究院",
    school: "智能科学与技术学院",
    department:
      "中国科学院软件研究所、国科大杭州高等研究院智能科学与技术学院",
  });

  assert.equal(suggestion.action, "reject_group");
  assert.equal(suggestion.kind, "mixed_organizations");
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

test("学院和研究院需要判断层级，研究所默认作为学院下级", () => {
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
  assert.equal(researchInstitute, null);
});

test("系所误填学院时优先匹配同校同级机构", () => {
  const sibling = {
    id: "org_example_cs",
    type: "school",
    canonical_name: "计算机学院",
    aliases: [],
    lineage_names: ["示例大学", "计算机学院"],
  };
  const otherUniversity = {
    id: "org_other_cs",
    type: "school",
    canonical_name: "计算机学院",
    aliases: [],
    lineage_names: ["其他大学", "计算机学院"],
  };
  const candidate = logic.siblingOrganizationCandidate(
    {
      university: "示例大学",
      school: "电子学院",
      department: "计算机学院",
    },
    [otherUniversity, sibling],
  );

  assert.equal(candidate.id, "org_example_cs");
  assert.equal(
    logic.siblingOrganizationCandidate(
      {
        university: "示例大学",
        school: "电子学院",
        department: "先进计算研究所",
      },
      [sibling],
    ),
    null,
  );
});

test("学院层级默认值区分上级别名、同级学院和新建同级学院", () => {
  const submitted = {
    university: "北京大学",
    school: "电子学院",
    department: "化学学院",
  };
  const createSibling = logic.schoolLevelPlacementDefault(submitted, []);
  assert.equal(createSibling.action, "create_sibling");
  assert.equal(createSibling.organizationType, "school");
  assert.equal(createSibling.canonicalName, "化学学院");

  const similarName = logic.schoolLevelPlacementDefault(
    { ...submitted, department: "电子科技学院" },
    [],
  );
  assert.equal(similarName.action, "create_sibling");

  const useParent = logic.schoolLevelPlacementDefault(
    { ...submitted, department: "北京大学电子学院" },
    [],
  );
  assert.equal(useParent.action, "use_parent");
  assert.ok(useParent.score >= 95);

  const sibling = {
    id: "org_pku_chemistry",
    type: "school",
    canonical_name: "化学学院",
    aliases: [],
    lineage_names: ["北京大学", "化学学院"],
  };
  const useSibling = logic.schoolLevelPlacementDefault(submitted, [sibling]);
  assert.equal(useSibling.action, "use_existing");
  assert.equal(useSibling.organization.id, sibling.id);

  const rejectMixed = logic.schoolLevelPlacementDefault(
    {
      ...submitted,
      department: "中国科学院计算技术研究所、中国科学院大学计算机科学与技术学院",
    },
    [],
  );
  assert.equal(rejectMixed.action, "reject_group");
});

test("同级学院只接受明确名称证据，模糊相似不自动归并", () => {
  const placement = logic.schoolLevelPlacementDefault(
    {
      university: "北京交通大学",
      school: "计算机科学与技术学院",
      department: "网络空间安全学院",
    },
    [
      {
        id: "org_bjtu_security",
        type: "school",
        canonical_name: "网络空间安全学院 国家保密学院",
        aliases: [],
        lineage_names: ["北京交通大学", "网络空间安全学院 国家保密学院"],
      },
    ],
  );

  assert.equal(placement.action, "use_existing");
  assert.equal(placement.organization.id, "org_bjtu_security");
  assert.ok(placement.score >= 95);

  const tiedPlacement = logic.schoolLevelPlacementDefault(
    {
      university: "北京交通大学",
      school: "计算机科学与技术学院",
      department: "计算机学院",
    },
    [
      {
        id: "org_bjtu_computer_information",
        type: "school",
        canonical_name: "计算机与信息技术学院",
        aliases: [],
        lineage_names: ["北京交通大学", "计算机与信息技术学院"],
      },
    ],
  );
  assert.equal(tiedPlacement.action, "create_sibling");

  const historicalAliasPlacement = logic.schoolLevelPlacementDefault(
    {
      university: "北京交通大学",
      school: "计算机科学与技术学院",
      department: "计算机与信息技术学院",
    },
    [
      {
        id: "org_bjtu_computer_information",
        type: "school",
        canonical_name: "计算机与信息技术学院",
        aliases: [],
        lineage_names: ["北京交通大学", "计算机与信息技术学院"],
      },
    ],
  );
  assert.equal(historicalAliasPlacement.action, "use_existing");
  assert.equal(
    historicalAliasPlacement.organization.id,
    "org_bjtu_computer_information",
  );
});

test("学院名称相似度只把明确变体用于自动判断", () => {
  assert.ok(logic.schoolOrganizationNameScore("电子学院", "电子科技学院") < 95);
  assert.ok(
    logic.schoolOrganizationNameScore(
      "计算机科学与技术学院",
      "计算机与信息技术学院",
    ) < 95,
  );
  assert.ok(
    logic.schoolOrganizationNameScore("光电与智能研究院", "光电与智能学院") >= 90,
  );
  assert.ok(
    logic.schoolOrganizationNameScore(
      "智能科学与技术学院",
      "国科大杭州高等研究院智能学院",
      ["国科大杭州高等研究院"],
    ) < 95,
  );
  assert.equal(logic.schoolOrganizationNameScore("电子学院", "化学学院"), 0);
  assert.ok(
    logic.schoolOrganizationNameScore(
      "智能科学与技术学院",
      "计算机科学与技术学院",
    ) < 70,
  );
});

test("学院名称只有唯一高置信候选时才默认归入已有机构", () => {
  const computerSchool = {
    id: "org_bjtu_computer",
    type: "school",
    canonical_name: "计算机科学与技术学院",
    aliases: ["计算机与信息技术学院"],
  };
  const unrelated = {
    id: "org_bjtu_security",
    type: "school",
    canonical_name: "网络空间安全学院",
    aliases: [],
  };
  const matched = logic.schoolOrganizationCandidateMatch(
    "计算机与信息技术学院",
    [unrelated, computerSchool],
  );
  assert.equal(matched.organization.id, computerSchool.id);
  assert.equal(matched.score, 100);

  assert.equal(
    logic.schoolOrganizationCandidateMatch(
      "电子科技学院",
      [{ id: "first", type: "school", canonical_name: "电子学院", aliases: [] }],
    ),
    null,
  );
});

test("同邮箱导师的现有任职形成多数证据时才建议学院", () => {
  const organizations = [
    {
      id: "university",
      type: "university",
      canonical_name: "示例大学",
      lineage_ids: ["university"],
    },
    {
      id: "school_a",
      type: "school",
      canonical_name: "甲学院",
      lineage_ids: ["university", "school_a"],
    },
    {
      id: "department_a",
      type: "department",
      canonical_name: "甲系",
      lineage_ids: ["university", "school_a", "department_a"],
    },
    {
      id: "school_b",
      type: "school",
      canonical_name: "乙学院",
      lineage_ids: ["university", "school_b"],
    },
  ];
  const identityRow = (organizationId) => ({
    identity: {
      requires_resolution: true,
      mentor: {
        affiliations: [{ status: "current", organization_id: organizationId }],
      },
    },
  });

  const evidence = logic.identitySchoolEvidence(
    [identityRow("department_a"), identityRow("department_a")],
    organizations,
  );
  assert.equal(evidence.organization.id, "school_a");
  assert.equal(evidence.votes, 2);

  assert.equal(
    logic.identitySchoolEvidence(
      [identityRow("department_a"), identityRow("school_b")],
      organizations,
    ),
    null,
  );
  assert.equal(
    logic.identitySchoolEvidence([identityRow("department_a")], organizations),
    null,
  );
});

test("系所名称重复学校时默认归到学校而不是当前学院", () => {
  const suggestion = logic.pathReviewSuggestion({
    university: "国科大杭州高等研究院",
    school: "智能科学与技术学院",
    department: "杭州高等研究院",
  });

  assert.equal(suggestion.action, "use_ancestor");
  assert.equal(suggestion.targetLevel, "university");
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

test("机构搜索支持同时输入学校和学院并优先完整路径", () => {
  const school = {
    id: "org_example_cs",
    canonical_name: "计算机学院",
    aliases: [],
    lineage_names: ["示例大学", "计算机学院"],
    approved_domains: [],
  };
  const department = {
    id: "org_example_ai",
    canonical_name: "人工智能研究所",
    aliases: [],
    lineage_names: ["示例大学", "计算机学院", "人工智能研究所"],
    approved_domains: [],
  };
  const other = {
    id: "org_other_cs",
    canonical_name: "计算机学院",
    aliases: [],
    lineage_names: ["其他大学", "计算机学院"],
    approved_domains: [],
  };

  assert.deepEqual(
    Array.from(
      logic.rankOrganizationSearchResults(
        [department, other, school],
        "示例大学 计算机学院",
      ),
      (organization) => organization.id,
    ),
    ["org_example_cs", "org_example_ai"],
  );
  assert.deepEqual(
    Array.from(
      logic.rankOrganizationSearchResults(
        [other, school],
        "示例大学计算机学院",
      ),
      (organization) => organization.id,
    ),
    ["org_example_cs"],
  );
});

test("官方发现来源可以核验，外部个人主页不会被误认为官方域名", () => {
  assert.equal(
    logic.hasOfficialEvidence(
      ["https://faculty.example.edu/mentor/1"],
      ["example.edu"],
    ),
    true,
  );
  assert.equal(
    logic.hasOfficialEvidence(
      ["https://scholar.google.com/citations?user=example"],
      ["example.edu"],
    ),
    false,
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

test("审核评论复用重复机构层级并保持原决策不变", () => {
  const levels = [
    {
      level: "university",
      action: "existing",
      organization_id: "org_example_university",
      organization_type: null,
      canonical_name: null,
      official_url: null,
      approved_domains: [],
      save_submitted_as_alias: false,
    },
    {
      level: "school",
      action: "existing",
      organization_id: "org_example_school",
      organization_type: null,
      canonical_name: null,
      official_url: null,
      approved_domains: [],
      save_submitted_as_alias: false,
    },
    {
      level: "department",
      action: "skip",
      organization_id: null,
      organization_type: null,
      canonical_name: null,
      official_url: null,
      approved_domains: [],
      save_submitted_as_alias: false,
    },
  ];
  const decision = {
    schema_version: 1,
    kind: "batch_organization_review_decision",
    pull_request_number: 27,
    issue_number: 26,
    manifest_sha256: "a".repeat(64),
    organization_creations: [],
    decisions: Array.from({ length: 12 }, (_, index) => ({
      group_id: `org_group_${index.toString(16).padStart(16, "0")}`,
      action: "resolve",
      reason: null,
      levels,
      target_organization_id: null,
      mapping_kind: "standard",
      mapping_reason: null,
      save_path_correction: false,
      row_overrides: [],
      identity_resolutions: [],
    })),
  };
  const originalJson = JSON.stringify(decision);
  const compact = JSON.parse(
    JSON.stringify(logic.compactDecisionForComment(decision)),
  );
  const compactJson = JSON.stringify(compact);

  assert.equal(compact.encoding, "shared_levels_v1");
  assert.equal(compact.level_decisions.length, 3);
  assert.equal(Object.hasOwn(compact, "organization_creations"), false);
  assert.deepEqual(compact.decisions[0].level_refs, [0, 1, 2]);
  assert.equal(Object.hasOwn(compact.decisions[0], "levels"), false);
  assert.equal(compactJson.length < originalJson.length * 0.45, true);
  assert.equal(JSON.stringify(decision), originalJson);
});
