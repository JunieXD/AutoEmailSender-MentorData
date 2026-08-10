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

  function compactOrganizationName(value) {
    return normalizeOrganizationName(value).replace(/[-—–_/:：|]/gu, "");
  }

  function pathReviewSuggestion(submitted, suggestion = null) {
    const university = String(submitted?.university || "").normalize("NFKC").trim();
    const school = String(submitted?.school || "").normalize("NFKC").trim();
    const department = String(submitted?.department || "").normalize("NFKC").trim();
    const universityKey = compactOrganizationName(university);
    const schoolKey = compactOrganizationName(school);
    const departmentKey = compactOrganizationName(department);

    if (!departmentKey) {
      return null;
    }
    if (typeof suggestion?.target_organization_id === "string") {
      return {
        kind: "known_destination",
        action: "use_existing",
        targetId: suggestion.target_organization_id,
        confidence: suggestion.source === "history" ? "certain" : "high",
        title: "采用系统找到的机构",
        reason: String(suggestion.reason || "系统找到了与投稿路径对应的已有机构。"),
      };
    }
    if (schoolKey && departmentKey === schoolKey) {
      return {
        kind: "same_as_parent",
        action: "use_parent",
        targetLevel: "school",
        confidence: "certain",
        title: `归入「${school}」`,
        reason: "系所内容与学院名称相同，不需要再建立一级机构。",
      };
    }
    if (
      schoolKey &&
      departmentKey.length > schoolKey.length &&
      departmentKey.endsWith(schoolKey)
    ) {
      return {
        kind: "parent_name_with_prefix",
        action: "use_parent",
        targetLevel: "school",
        confidence: "high",
        title: `归入「${school}」`,
        reason: "系所名称以当前学院全名结尾，前面的内容更像学校或校区说明。请确认它是否只是学院的另一种写法。",
      };
    }
    if (
      universityKey &&
      (departmentKey === universityKey ||
        (universityKey.length > departmentKey.length && universityKey.endsWith(departmentKey)))
    ) {
      return {
        kind: "repeated_ancestor",
        action: "use_parent",
        targetLevel: "school",
        confidence: "review",
        title: "不单独建立这一层",
        reason: "系所内容与上级学校名称相同或高度重合，可能是重复填写的上级机构。",
      };
    }
    if (correctionKindForDepartment(department, school)) {
      return {
        kind: "ambiguous_hierarchy",
        action: "review_hierarchy",
        confidence: "review",
        title: "判断这一层的实际归属",
        reason:
          String(suggestion?.reason || "这个系所名称看起来可能是学院或研究院，需要确认它的实际层级。"),
      };
    }
    return null;
  }

  function rankOrganizationCandidates(context, organizations) {
    const submitted = context?.submitted || {};
    const submittedKeys = new Set(
      [submitted.university, submitted.school, submitted.department]
        .map(compactOrganizationName)
        .filter(Boolean),
    );
    const universityKey = compactOrganizationName(submitted.university);
    const schoolKey = compactOrganizationName(submitted.school);
    const sourceDomains = new Set(
      (context?.source_domains || []).map((value) => String(value).toLocaleLowerCase()),
    );
    const scored = organizations.map((organization, index) => {
      const names = [organization.canonical_name, ...(organization.aliases || [])]
        .map(compactOrganizationName)
        .filter(Boolean);
      const lineageKeys = (organization.lineage_names || [])
        .map(compactOrganizationName)
        .filter(Boolean);
      let score = 0;
      if (names.some((name) => submittedKeys.has(name))) {
        score += 1000;
      }
      if (organization.pending) {
        score += 120;
      }
      if (universityKey && lineageKeys.includes(universityKey)) {
        score += 260;
      }
      if (schoolKey && lineageKeys.includes(schoolKey)) {
        score += 220;
      }
      const domainMatches = (organization.approved_domains || []).filter((domain) =>
        sourceDomains.has(String(domain).toLocaleLowerCase()),
      ).length;
      score += Math.min(domainMatches, 2) * 120;
      return { organization, index, score };
    });
    scored.sort(
      (first, second) =>
        second.score - first.score ||
        first.index - second.index ||
        String(first.organization.canonical_name).localeCompare(
          String(second.organization.canonical_name),
          "zh-CN",
        ),
    );
    return scored.map(({ organization }) => organization);
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
    if (
      submittedDepartment.endsWith("研究院") ||
      submittedDepartment.endsWith("研究所")
    ) {
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
        ? "投稿中的系所名称像独立研究机构，需要确认它是否属于当前学院。"
        : kind === "department_as_school"
          ? "投稿中的系所名称以“学院”结尾，需要确认它是否与当前学院同级。"
          : "投稿机构路径需要纠正";
    const targetId =
      typeof suggestion?.target_organization_id === "string"
        ? suggestion.target_organization_id
        : null;
    return {
      mode: targetId ? "corrected" : "standard",
      targetAction: "existing",
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
      needsReview: true,
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
    compactOrganizationName,
    mergeIndependentCreations,
    normalizeOrganizationName,
    organizationTypeForCorrection,
    pathReviewSuggestion,
    rankOrganizationCandidates,
    requiredSubmittedLevels,
  });
})(globalThis);
