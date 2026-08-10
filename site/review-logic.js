(function exposeMentorReviewLogic(scope) {
  "use strict";

  const LEVELS = ["university", "school", "department"];
  const SCHOOL_TYPES = new Set(["school", "institute"]);
  const COMPACT_DECISION_ENCODING = "shared_levels_v1";

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

  function organizationSearchTokens(value) {
    return String(value || "")
      .normalize("NFKC")
      .toLocaleLowerCase()
      .split(/[\s/\\|>›→,，;；·•・()（）[\]【】_-]+/u)
      .map(compactOrganizationName)
      .filter(Boolean);
  }

  function rankOrganizationSearchResults(organizations, query) {
    const tokens = organizationSearchTokens(query);
    if (!tokens.length) {
      return [...organizations];
    }
    const compactQuery = compactOrganizationName(query);
    const lastToken = tokens[tokens.length - 1];
    return organizations
      .map((organization, index) => {
        const canonicalName = compactOrganizationName(organization.canonical_name);
        const aliases = (organization.aliases || [])
          .map(compactOrganizationName)
          .filter(Boolean);
        const lineage = (organization.lineage_names || [])
          .map(compactOrganizationName)
          .filter(Boolean);
        const lineageText = lineage.join("");
        const searchable = [
          canonicalName,
          ...aliases,
          ...lineage,
          lineageText,
          compactOrganizationName(organization.id),
          ...(organization.approved_domains || []).map(compactOrganizationName),
        ].filter(Boolean);
        if (!tokens.every((token) => searchable.some((field) => field.includes(token)))) {
          return null;
        }

        let score = 0;
        if (lineageText === compactQuery) {
          score += 2200;
        } else if (lineageText.includes(compactQuery)) {
          score += 320;
        }
        if (canonicalName === compactQuery) {
          score += 1800;
        }
        if (aliases.includes(compactQuery)) {
          score += 1500;
        }
        if (canonicalName === lastToken || aliases.includes(lastToken)) {
          score += 900;
        }
        for (const token of tokens) {
          if (lineage.includes(token)) {
            score += 320;
          } else if (searchable.some((field) => field.startsWith(token))) {
            score += 120;
          } else {
            score += 40;
          }
        }
        score -= Math.max(0, lineage.length - tokens.length) * 8;
        return { organization, index, score };
      })
      .filter(Boolean)
      .sort((first, second) => second.score - first.score || first.index - second.index)
      .map(({ organization }) => organization);
  }

  function hasOfficialEvidence(urls, domains) {
    const normalizedDomains = (domains || [])
      .map((domain) => String(domain || "").toLocaleLowerCase().replace(/\.$/u, ""))
      .filter(Boolean);
    return (urls || []).some((url) => {
      try {
        const hostname = new URL(url).hostname.toLocaleLowerCase().replace(/\.$/u, "");
        return normalizedDomains.some(
          (domain) => hostname === domain || hostname.endsWith(`.${domain}`),
        );
      } catch {
        return false;
      }
    });
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
        title: "采用已有归属",
        reason: String(suggestion.reason || "已找到对应机构。"),
      };
    }
    if (schoolKey && departmentKey === schoolKey) {
      return {
        kind: "same_as_parent",
        action: "use_parent",
        targetLevel: "school",
        confidence: "certain",
        title: `归入「${school}」`,
        reason: "与学院同名，无需重复建立。",
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
        reason: "名称可能只是当前学院的另一种写法。",
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
        reason: "名称与上级学校重复。",
      };
    }
    if (department.endsWith("研究所")) {
      return null;
    }
    if (correctionKindForDepartment(department, school)) {
      return {
        kind: "ambiguous_hierarchy",
        action: "review_hierarchy",
        confidence: "review",
        title: "确认实际层级",
        reason:
          String(suggestion?.reason || "名称像学院或研究院。"),
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
    const suggestedTargetId =
      typeof suggestion?.target_organization_id === "string"
        ? suggestion.target_organization_id
        : null;
    const submittedDepartment = String(submitted?.department || "").normalize("NFKC").trim();
    if (submittedDepartment.endsWith("研究所") && !suggestedTargetId) {
      return null;
    }
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
        ? "名称像独立研究机构，请确认层级。"
        : kind === "department_as_school"
          ? "名称像学院，请确认层级。"
          : "请确认机构归属";
    const targetId = suggestedTargetId;
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

  function compactDecisionForComment(decision) {
    const {
      organization_creations: organizationCreations = [],
      decisions = [],
      ...metadata
    } = decision;
    const levelDecisions = [];
    const levelDecisionIndex = new Map();
    const compactDecisions = decisions.map((groupDecision) => {
      const { levels = [], ...groupMetadata } = groupDecision;
      const levelRefs = levels.map((levelDecision) => {
        const key = JSON.stringify(levelDecision);
        let index = levelDecisionIndex.get(key);
        if (index === undefined) {
          index = levelDecisions.length;
          levelDecisionIndex.set(key, index);
          levelDecisions.push(levelDecision);
        }
        return index;
      });
      return {
        ...groupMetadata,
        level_refs: levelRefs,
      };
    });
    return {
      ...metadata,
      encoding: COMPACT_DECISION_ENCODING,
      ...(organizationCreations.length
        ? { organization_creations: organizationCreations }
        : {}),
      level_decisions: levelDecisions,
      decisions: compactDecisions,
    };
  }

  scope.MentorReviewLogic = Object.freeze({
    compactDecisionForComment,
    correctionDefaults,
    correctionKindForDepartment,
    compactOrganizationName,
    hasOfficialEvidence,
    mergeIndependentCreations,
    normalizeOrganizationName,
    organizationTypeForCorrection,
    pathReviewSuggestion,
    rankOrganizationCandidates,
    rankOrganizationSearchResults,
    requiredSubmittedLevels,
  });
})(globalThis);
