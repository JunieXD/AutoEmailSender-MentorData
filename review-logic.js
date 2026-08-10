(function exposeMentorReviewLogic(scope) {
  "use strict";

  const LEVELS = ["university", "school", "department"];
  const SCHOOL_TYPES = new Set(["school", "institute"]);
  const COMPACT_DECISION_ENCODING = "shared_levels_v1";
  // Automatic placement requires explicit name evidence. Shared discipline words
  // alone do not prove that two colleges are the same organization.
  const SCHOOL_NAME_MATCH_THRESHOLD = 95;
  const SCHOOL_AUTO_MATCH_THRESHOLD = 95;
  const SIBLING_NAME_MATCH_THRESHOLD = 95;

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

  function stripSchoolLevelSuffix(value) {
    return String(value || "").replace(/(?:学院|研究院)$/u, "");
  }

  function mixedOrganizationCount(value) {
    return String(value || "")
      .normalize("NFKC")
      .split(/[,，、;；]+/u)
      .map((part) => part.trim())
      .filter((part) => /(?:学院|研究院|研究所)$/u.test(part)).length;
  }

  function schoolNameVariants(value, contextNames = []) {
    const raw = String(value || "").normalize("NFKC").toLocaleLowerCase().trim();
    const parts = raw
      .split(/[\s/\\|>›→,，;；、·•・()（）[\]【】_-]+/u)
      .map(compactOrganizationName)
      .filter(Boolean);
    const contexts = contextNames.map(compactOrganizationName).filter(Boolean);
    const variants = new Set([compactOrganizationName(raw), ...parts]);
    for (const candidate of [...variants]) {
      for (const context of contexts) {
        if (candidate.startsWith(context) && candidate.length > context.length) {
          variants.add(candidate.slice(context.length));
        }
      }
    }
    for (const candidate of [...variants]) {
      const stem = stripSchoolLevelSuffix(candidate);
      if (stem) {
        variants.add(stem);
      }
    }
    return [...variants].filter(Boolean);
  }

  function commonPrefixLength(first, second) {
    const maximum = Math.min(first.length, second.length);
    let length = 0;
    while (length < maximum && first[length] === second[length]) {
      length += 1;
    }
    return length;
  }

  function bigramDiceScore(first, second) {
    if (first.length < 2 || second.length < 2) {
      return 0;
    }
    const counts = new Map();
    for (let index = 0; index < first.length - 1; index += 1) {
      const value = first.slice(index, index + 2);
      counts.set(value, (counts.get(value) || 0) + 1);
    }
    let overlap = 0;
    for (let index = 0; index < second.length - 1; index += 1) {
      const value = second.slice(index, index + 2);
      const count = counts.get(value) || 0;
      if (count > 0) {
        overlap += 1;
        counts.set(value, count - 1);
      }
    }
    return (2 * overlap) / (first.length + second.length - 2);
  }

  function schoolOrganizationNameScore(first, second, contextNames = []) {
    const firstVariants = schoolNameVariants(first, contextNames);
    const secondVariants = schoolNameVariants(second, contextNames);
    let score = 0;
    for (const firstVariant of firstVariants) {
      for (const secondVariant of secondVariants) {
        if (firstVariant === secondVariant) {
          score = Math.max(score, 100);
          continue;
        }
        const firstStem = stripSchoolLevelSuffix(firstVariant);
        const secondStem = stripSchoolLevelSuffix(secondVariant);
        if (!firstStem || !secondStem) {
          continue;
        }
        if (firstStem === secondStem) {
          score = Math.max(score, 96);
          continue;
        }
        const shorterLength = Math.min(firstStem.length, secondStem.length);
        const longerLength = Math.max(firstStem.length, secondStem.length);
        if (
          shorterLength >= 2 &&
          (firstStem.includes(secondStem) || secondStem.includes(firstStem))
        ) {
          const coverage = shorterLength / longerLength;
          score = Math.max(
            score,
            shorterLength === 2 ? 72 : Math.round(82 + coverage * 8),
          );
        }
        const prefixLength = commonPrefixLength(firstStem, secondStem);
        if (prefixLength >= 2 && prefixLength / shorterLength >= 0.6) {
          score = Math.max(
            score,
            Math.round(60 + (prefixLength / shorterLength) * 18),
          );
        }
        const dice = bigramDiceScore(firstStem, secondStem);
        if (prefixLength >= 2 && dice >= 0.58) {
          score = Math.max(score, Math.round(58 + dice * 25));
        }
      }
    }
    return Math.min(score, 100);
  }

  function parentOrganizationNameMatch(submitted) {
    const score = schoolOrganizationNameScore(
      submitted?.school,
      submitted?.department,
      [submitted?.university],
    );
    if (score < SCHOOL_NAME_MATCH_THRESHOLD) {
      return null;
    }
    return {
      score,
      confidence: score >= 90 ? "high" : "review",
      reason:
        score >= 90
          ? "名称与当前学院相同或只是写法不同。"
          : "名称与当前学院较为接近。",
    };
  }

  function schoolOrganizationCandidateMatch(
    submittedName,
    organizations,
    contextNames = [],
  ) {
    const candidates = (organizations || [])
      .filter((organization) => SCHOOL_TYPES.has(organization?.type))
      .map((organization) => {
        const names = [organization.canonical_name, ...(organization.aliases || [])]
          .filter(Boolean);
        const score = Math.max(
          0,
          ...names.map((name) =>
            schoolOrganizationNameScore(submittedName, name, contextNames),
          ),
        );
        return { organization, score };
      })
      .filter(({ score }) => score >= SCHOOL_AUTO_MATCH_THRESHOLD)
      .sort(
        (first, second) =>
          second.score - first.score ||
          String(first.organization.canonical_name).localeCompare(
            String(second.organization.canonical_name),
            "zh-CN",
          ),
      );
    if (!candidates.length || candidates[0].score === candidates[1]?.score) {
      return null;
    }
    return candidates[0];
  }

  function identitySchoolEvidence(rows, organizations) {
    const organizationById = new Map(
      (organizations || [])
        .filter((organization) => typeof organization?.id === "string")
        .map((organization) => [organization.id, organization]),
    );
    const votes = new Map();
    let evidenceRows = 0;
    for (const row of rows || []) {
      if (row?.identity?.requires_resolution !== true) {
        continue;
      }
      const schoolIds = new Set();
      for (const affiliation of row.identity.mentor?.affiliations || []) {
        if (affiliation?.status !== "current") {
          continue;
        }
        const organization = organizationById.get(affiliation.organization_id);
        if (!organization) {
          continue;
        }
        const school = [...(organization.lineage_ids || [organization.id])]
          .reverse()
          .map((organizationId) => organizationById.get(organizationId))
          .find((candidate) => SCHOOL_TYPES.has(candidate?.type));
        if (school) {
          schoolIds.add(school.id);
        }
      }
      if (schoolIds.size !== 1) {
        continue;
      }
      const [schoolId] = schoolIds;
      evidenceRows += 1;
      votes.set(schoolId, (votes.get(schoolId) || 0) + 1);
    }
    const ranked = [...votes.entries()].sort(
      ([firstId, firstVotes], [secondId, secondVotes]) =>
        secondVotes - firstVotes || firstId.localeCompare(secondId),
    );
    if (!ranked.length) {
      return null;
    }
    const [organizationId, topVotes] = ranked[0];
    const secondVotes = ranked[1]?.[1] || 0;
    if (
      topVotes < 2 ||
      topVotes === secondVotes ||
      topVotes / evidenceRows < 0.75
    ) {
      return null;
    }
    return {
      organization: organizationById.get(organizationId),
      votes: topVotes,
      evidenceRows,
    };
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
    if (mixedOrganizationCount(department) > 1) {
      return {
        kind: "mixed_organizations",
        action: "reject_group",
        correctionKind:
          correctionKindForDepartment(department, school) || "custom",
        confidence: "review",
        title: "不收录这组",
        reason: "系所字段混合了多个机构，无法确定唯一归属。",
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
    const parentMatch = parentOrganizationNameMatch(submitted);
    if (parentMatch) {
      return {
        kind: "parent_name_with_prefix",
        action: "use_parent",
        targetLevel: "school",
        confidence: parentMatch.confidence,
        title: `归入「${school}」`,
        reason: parentMatch.reason,
      };
    }
    if (
      universityKey &&
      (departmentKey === universityKey ||
        (universityKey.length > departmentKey.length && universityKey.endsWith(departmentKey)))
    ) {
      return {
        kind: "repeated_ancestor",
        action: "use_ancestor",
        targetLevel: "university",
        confidence: departmentKey === universityKey ? "certain" : "high",
        title: `归入「${university}」`,
        reason: "名称与学校相同或只是学校名称的另一种写法。",
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

  function siblingOrganizationMatch(submitted, organizations) {
    const kind = correctionKindForDepartment(
      submitted?.department,
      submitted?.school,
    );
    const expectedType =
      kind === "department_as_school"
        ? "school"
        : kind === "department_as_institute"
          ? "institute"
          : null;
    if (!expectedType) {
      return null;
    }
    const universityKey = compactOrganizationName(submitted?.university);
    const departmentKey = compactOrganizationName(submitted?.department);
    if (!universityKey || !departmentKey) {
      return null;
    }
    const schoolKey = compactOrganizationName(submitted?.school);
    const candidates = (organizations || [])
      .map((organization) => {
        if (organization?.type !== expectedType) {
          return null;
        }
        const names = [organization.canonical_name, ...(organization.aliases || [])]
          .filter(Boolean);
        const compactNames = names.map(compactOrganizationName).filter(Boolean);
        const lineage = (organization.lineage_names || [])
          .map(compactOrganizationName)
          .filter(Boolean);
        if (
          lineage[0] !== universityKey ||
          (schoolKey && compactNames.includes(schoolKey))
        ) {
          return null;
        }
        const score = Math.max(
          0,
          ...names.map((name) =>
            schoolOrganizationNameScore(
              submitted?.department,
              name,
              [submitted?.university],
            ),
          ),
        );
        return score >= SIBLING_NAME_MATCH_THRESHOLD
          ? { organization, score }
          : null;
      })
      .filter(Boolean)
      .sort((first, second) => {
        const firstCanonical =
          compactOrganizationName(first.organization.canonical_name) === departmentKey ? 1 : 0;
        const secondCanonical =
          compactOrganizationName(second.organization.canonical_name) === departmentKey ? 1 : 0;
        return (
          second.score - first.score ||
          secondCanonical - firstCanonical ||
          (first.organization.lineage_names || []).length -
            (second.organization.lineage_names || []).length ||
          String(first.organization.canonical_name).localeCompare(
            String(second.organization.canonical_name),
            "zh-CN",
          )
        );
      });
    return candidates[0] || null;
  }

  function siblingOrganizationCandidate(submitted, organizations) {
    return siblingOrganizationMatch(submitted, organizations)?.organization || null;
  }

  function schoolLevelPlacementDefault(submitted, organizations = []) {
    const correctionKind = correctionKindForDepartment(
      submitted?.department,
      submitted?.school,
    );
    if (!correctionKind) {
      return null;
    }
    const submittedDepartment = String(submitted?.department || "").normalize("NFKC").trim();
    if (mixedOrganizationCount(submittedDepartment) > 1) {
      return {
        action: "reject_group",
        correctionKind,
        reason: "系所字段混合了多个机构，无法确定唯一归属。",
      };
    }
    const sibling = siblingOrganizationMatch(submitted, organizations);
    const parent = parentOrganizationNameMatch(submitted);
    if (parent) {
      return {
        action: "use_parent",
        correctionKind,
        ...parent,
      };
    }
    if (sibling) {
      return {
        action: "use_existing",
        correctionKind,
        organization: sibling.organization,
        score: sibling.score,
      };
    }
    if (parent) {
      return {
        action: "use_parent",
        correctionKind,
        ...parent,
      };
    }
    return {
      action: "create_sibling",
      correctionKind,
      canonicalName: submittedDepartment,
      organizationType:
        correctionKind === "department_as_institute" ? "institute" : "school",
    };
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
    identitySchoolEvidence,
    mergeIndependentCreations,
    normalizeOrganizationName,
    organizationTypeForCorrection,
    parentOrganizationNameMatch,
    pathReviewSuggestion,
    rankOrganizationCandidates,
    rankOrganizationSearchResults,
    requiredSubmittedLevels,
    schoolOrganizationCandidateMatch,
    schoolLevelPlacementDefault,
    schoolOrganizationNameScore,
    siblingOrganizationCandidate,
    siblingOrganizationMatch,
  });
})(globalThis);
