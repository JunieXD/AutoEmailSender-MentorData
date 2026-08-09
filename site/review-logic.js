(function exposeMentorReviewLogic(scope) {
  "use strict";

  const LEVELS = ["university", "school", "department"];
  const SCHOOL_TYPES = new Set(["school", "institute"]);

  function normalizeOrganizationName(value) {
    return String(value || "")
      .normalize("NFKC")
      .trim()
      .toLocaleLowerCase()
      .replace(/[\s·•・,，.。()（）[\]【】]/gu, "");
  }

  function correctionKindForDepartment(department, school) {
    const normalizedDepartment = normalizeOrganizationName(department);
    if (
      !normalizedDepartment ||
      normalizedDepartment === normalizeOrganizationName(school)
    ) {
      return null;
    }
    const submittedDepartment = String(department || "").normalize("NFKC").trim();
    if (submittedDepartment.endsWith("研究院")) {
      return "department_as_institute";
    }
    if (submittedDepartment.endsWith("学院")) {
      return "department_as_school";
    }
    return null;
  }

  function organizationTypeForCorrection(kind, canonicalName) {
    if (kind === "department_as_school") {
      return "school";
    }
    if (kind === "department_as_institute") {
      return "institute";
    }
    const name = String(canonicalName || "").normalize("NFKC").trim();
    if (name.endsWith("研究院") || name.endsWith("研究所")) {
      return "institute";
    }
    if (name.endsWith("学院")) {
      return "school";
    }
    if (name.endsWith("实验室") || name.endsWith("研究室")) {
      return "laboratory";
    }
    if (name.endsWith("中心")) {
      return "center";
    }
    return "department";
  }

  function correctionDefaults(submitted, suggestion) {
    const serverKind = suggestion?.kind;
    const inferredKind = correctionKindForDepartment(
      submitted?.department,
      submitted?.school,
    );
    const kind = ["department_as_school", "department_as_institute", "custom"].includes(
      serverKind,
    )
      ? serverKind
      : inferredKind;
    if (!kind) {
      return null;
    }
    const canonicalName = String(submitted?.department || "").normalize("NFKC").trim();
    const defaultReason =
      kind === "department_as_institute"
        ? "系所字段以“研究院”结尾，疑似同校平级研究院"
        : kind === "department_as_school"
          ? "系所字段以“学院”结尾，疑似同校平级学院"
          : "投稿机构路径需要纠正";
    const targetId =
      typeof suggestion?.target_organization_id === "string"
        ? suggestion.target_organization_id
        : null;
    return {
      mode: "corrected",
      targetAction: targetId ? "existing" : "create",
      targetId,
      kind,
      reason:
        typeof suggestion?.reason === "string" && suggestion.reason.trim()
          ? suggestion.reason.trim()
          : defaultReason,
      source: suggestion?.source === "history" ? "history" : "heuristic",
      savePathCorrection: true,
      canonicalName,
      organizationType: organizationTypeForCorrection(kind, canonicalName),
      parentMode: "group",
    };
  }

  function requiredSubmittedLevels({
    mappingMode,
    targetAction,
    parentMode,
    organizationType,
  }) {
    if (mappingMode !== "corrected") {
      return [...LEVELS];
    }
    if (
      targetAction !== "create" ||
      parentMode !== "group" ||
      organizationType === "university"
    ) {
      return [];
    }
    if (SCHOOL_TYPES.has(organizationType)) {
      return ["university"];
    }
    return ["university", "school"];
  }

  function mergeIndependentCreations(creations, referencedIds) {
    const byId = new Map();
    for (const creation of creations) {
      const existing = byId.get(creation.organization_id);
      if (!existing) {
        byId.set(creation.organization_id, {
          ...creation,
          approved_domains: [...new Set(creation.approved_domains || [])].sort(),
        });
        continue;
      }
      for (const key of ["organization_type", "canonical_name", "parent_id"]) {
        if (existing[key] !== creation[key]) {
          throw new Error(`本次新建机构 ${creation.organization_id} 的规范信息不一致`);
        }
      }
      if (
        existing.official_url &&
        creation.official_url &&
        existing.official_url !== creation.official_url
      ) {
        throw new Error(`本次新建机构 ${creation.organization_id} 填写了不同的官网`);
      }
      existing.official_url ||= creation.official_url;
      existing.approved_domains = [
        ...new Set([...existing.approved_domains, ...(creation.approved_domains || [])]),
      ].sort();
    }

    const requiredIds = new Set(referencedIds);
    const queue = [...requiredIds];
    while (queue.length) {
      const creation = byId.get(queue.pop());
      if (creation && typeof creation.parent_id === "string" && byId.has(creation.parent_id)) {
        if (!requiredIds.has(creation.parent_id)) {
          requiredIds.add(creation.parent_id);
          queue.push(creation.parent_id);
        }
      }
    }
    return {
      creations: [...byId.values()]
        .filter((creation) => requiredIds.has(creation.organization_id))
        .sort((first, second) => first.organization_id.localeCompare(second.organization_id)),
      unusedIds: [...byId.keys()].filter((organizationId) => !requiredIds.has(organizationId)).sort(),
    };
  }

  scope.MentorReviewLogic = Object.freeze({
    correctionDefaults,
    correctionKindForDepartment,
    mergeIndependentCreations,
    normalizeOrganizationName,
    organizationTypeForCorrection,
    requiredSubmittedLevels,
  });
})(globalThis);
