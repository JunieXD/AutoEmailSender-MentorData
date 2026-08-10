(function exposeReportReviewLogic(scope) {
  "use strict";

  const EMAIL_PATTERN = /[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+/giu;

  function uniqueLines(value, maximum = Infinity) {
    return [...new Set(String(value || "").split(/\r?\n/u).map((item) => item.trim()).filter(Boolean))]
      .slice(0, maximum);
  }

  function extractEmails(...values) {
    const matches = values.flatMap((value) => String(value || "").match(EMAIL_PATTERN) || []);
    return [...new Set(matches.map((value) => value.toLocaleLowerCase()))];
  }

  function suggestedEmail(proposal) {
    const current = new Set(
      (proposal?.before?.contacts || []).map((contact) =>
        String(contact?.value || "").toLocaleLowerCase(),
      ),
    );
    return extractEmails(proposal?.proposed?.value, proposal?.proposed?.explanation)
      .find((email) => !current.has(email)) || "";
  }

  function observedAt(value) {
    const candidate = value ? new Date(value) : new Date();
    if (Number.isNaN(candidate.getTime())) {
      throw new Error("观察时间无效");
    }
    return candidate.toISOString().replace(/\.\d{3}Z$/u, "Z");
  }

  function buildContacts(before, options) {
    const email = String(options.email || "").trim().toLocaleLowerCase();
    const sourceUrl = String(options.sourceUrl || "").trim();
    const primaryAffiliation = (before.affiliations || []).find((item) => item.is_primary);
    const fresh = {
      value: email,
      status: "current",
      is_primary: options.action !== "add_secondary",
      affiliation_id: primaryAffiliation?.id || null,
      source_url: sourceUrl,
      observed_at: observedAt(options.observedAt),
    };
    if (options.action === "replace_all") {
      return [fresh];
    }
    const contacts = (before.contacts || []).map((contact) => ({ ...contact }));
    const existing = contacts.find(
      (contact) => String(contact.value || "").toLocaleLowerCase() === email,
    );
    if (existing) {
      Object.assign(existing, fresh);
      if (options.action === "add_secondary") {
        existing.is_primary = false;
      }
    } else {
      contacts.push(fresh);
    }
    if (options.action === "replace_primary") {
      for (const contact of contacts) {
        const selected = String(contact.value || "").toLocaleLowerCase() === email;
        if (!selected && contact.is_primary) {
          contact.status = "former";
        }
        contact.is_primary = selected;
      }
    }
    return contacts;
  }

  function buildNames(before, primaryName) {
    const value = String(primaryName || "").trim();
    const names = (before.names || []).map((name) => ({ ...name }));
    const primary = names.find((name) => name.is_primary);
    if (primary) {
      primary.value = value;
    } else {
      names.push({ value, kind: "native", is_primary: true });
    }
    return names;
  }

  function buildProfiles(before, options) {
    const url = String(options.url || "").trim();
    const primaryAffiliation = (before.affiliations || []).find((item) => item.is_primary);
    const fresh = {
      url,
      status: "current",
      affiliation_id: primaryAffiliation?.id || null,
      observed_at: observedAt(options.observedAt),
    };
    const profiles = (before.profiles || []).map((profile) => ({ ...profile }));
    const existing = profiles.find((profile) => profile.url === url);
    if (existing) {
      Object.assign(existing, fresh);
    } else {
      profiles.push(fresh);
    }
    if (options.action === "replace_current") {
      for (const profile of profiles) {
        if (profile.url !== url && profile.status === "current") {
          profile.status = "unavailable";
        }
      }
    }
    return profiles;
  }

  function buildAffiliations(before, options) {
    const affiliations = (before.affiliations || []).map((item) => ({ ...item }));
    const primary = affiliations.find((item) => item.is_primary);
    if (!primary) {
      throw new Error("当前记录缺少可编辑的主要任职");
    }
    primary.organization_id = String(options.organizationId || "").trim();
    primary.title = String(options.title || "").trim() || null;
    primary.source_url = String(options.sourceUrl || "").trim();
    primary.observed_at = observedAt(options.observedAt);
    return affiliations;
  }

  scope.MentorReportReviewLogic = {
    buildAffiliations,
    buildContacts,
    buildNames,
    buildProfiles,
    extractEmails,
    observedAt,
    suggestedEmail,
    uniqueLines,
  };
})(typeof globalThis === "object" ? globalThis : window);
