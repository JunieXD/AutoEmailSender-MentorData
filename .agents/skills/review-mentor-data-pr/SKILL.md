---
name: review-mentor-data-pr
description: Review AutoEmailSender-MentorData batch contribution Pull Requests through the repository's Agent-oriented CLI. Use when Codex needs to normalize submitted university, school, institute, department, center, or laboratory fields; plan an organization tree; collect human decisions for ambiguous paths; preflight a moderation decision; submit an explicitly approved review; or monitor the resulting MentorData intake workflow.
---

# Review MentorData PR

Use the repository's `mentor-data review` CLI as the source of truth. Start with `mentor-data review brief --pr N`, which includes the environment check; the repository-local Skill is sufficient and must not be globally installed. Stop and report the CLI's `actions` when `ready` is false. Follow structured `next` commands directly; read the relevant subcommand help when the structured output is insufficient. Never reconstruct decision JSON manually.

## Preserve the review boundary

- Trust submitted mentor names, emails, titles, research fields, papers, and profile contents by default.
- Do not open, crawl, or verify mentor pages unless the user explicitly requests content verification.
- Treat source-domain validation performed by the trusted backend as a structural intake check, not an instruction to inspect page contents.
- Focus on institution normalization, organization-tree placement, conflicts already exposed by the review manifest, and safe intake.
- Use single-PR mode for isolated work and batch mode when the user asks for a consolidated review.
  In batch mode, keep a separate current draft, decision, and complete preflight for every PR.

## Collaborate with the user

1. Inspect and plan the selected PR with the CLI.
2. Let deterministic CLI rules resolve only high-confidence paths.
3. Immediately inspect `organization_change_preview` and `organization_conflicts` against the
   current global registry. Repeat this inspection after every answer that changes a path or name;
   do not wait until `check` to discover a cross-parent collision.
4. Fetch pending decision packets together with `review questions --details`, triage them using the
   policy below, and present only material ambiguities to the user.
5. Record decisions with the CLI, then repeat until no questions remain.
6. Offer one compact batch-level choice for saving reusable corrections when one or more questions expose
   `future-identical-path` in `path_correction_scopes`; otherwise describe the decision as
   current-batch only.
7. Run the full preflight and show every path normalization, organization create, update, rename,
   merge, official URL, and approved-domain change before any formal submission.

### Triage pending questions

Use the user's language and translate CLI choice values into short natural-language options. Keep
question IDs, organization IDs, command flags, and values such as `use-canonical` internal unless
the user explicitly asks for them. Never dump raw decision packets as the primary explanation.

When the user explicitly delegates obvious decisions, the agent may resolve only these reversible,
current-batch cases:

- remove an exactly repeated parent name or an exact parent prefix;
- accept a high-confidence `use-canonical` recommendation only when the candidate differs solely by
  that exact parent prefix;
- keep institutions separate when the only collision evidence is a common directory or roster,
  `list`, `index`, or equivalent shared listing page and there is no direct name-equivalence evidence;
- provide a `canonical_name` that only removes redundant parent text from a new child and resolves to
  the same planned organization.

Always require the user's decision for:

- identity or record conflicts, including reject, dual appointment, and transfer;
- a same-name institution under a different parent, which may represent intentional cross-unit
  placement rather than a duplicate;
- comma-, slash-, bracket-, or conjunction-separated multiple institutions;
- cross-parent mapping, school/institute sibling creation, rejection, merge, rename, or update;
- official URL or approved-domain changes;
- any future identical-path rule.

Treat `rule_default` as a mechanical fallback, not a recommendation. Present a non-null
`context_recommendation` only after checking its evidence. Shared-source relationships must never
carry a recommendation transitively through an unrelated third institution. Do not describe a
cross-parent same-name placement as data pollution without evidence that the two nodes should be one
entity.

For a consolidated review, plan every PR first, resolve deterministic items, and present all
remaining ambiguities together. Group the summary by PR, then render each PR's institution changes
as its own nested tree. Never use one PR's preflight as evidence that another PR is safe.
Use the compact `check-many` result for the consolidated outcome; read the returned local report
only for a failed PR or when exact changes are needed. Do not paste the full report into context.

Render organization changes as a nested Markdown tree that preserves the real hierarchy:
university, then school or institute, then department, center, or laboratory. Indent every child
below its parent in both the planning preview and the final pre-submit summary; never flatten nodes
from different levels into one list. Keep row counts and domain or URL changes on the applicable
node. In a multi-PR summary, make the PR heading the outer level and restart the institution tree
under it.

Use CLI filters, IDs, field projection, and organization search to keep context small. Read a full group or question only when its summary is insufficient.

## Protect formal approval

- Treat a marked organization-review PR comment as final approval because it immediately triggers the trusted promotion queue.
- Never run `review submit` without explicit authorization in the current conversation.
- For `submit-many`, require one explicit authorization that lists every PR number. The confirmed
  ordered PR list must exactly match the checked list; adding, omitting, deduplicating, or reordering
  PRs requires renewed authorization.
- Any decision mutation after preflight invalidates that preflight. Re-run the exact ordered check.
  Reuse an authorization from the same message only when the user explicitly requests the mutation
  and says to submit the listed PRs after applying it; otherwise request renewed authorization.
- Do not infer submission authority from permission to inspect, plan, answer, or preflight.
- Do not directly edit proposal JSON, the organization registry, the PR branch, Draft state, or merge state.
- Do not use `gh pr ready` or `gh pr merge`; let the existing trusted queue apply, finalize, and merge the review.
- If the CLI reports a stale PR or manifest, stop using the old draft, re-plan the current version, and surface any decisions that need confirmation again.
- After submission, run `review status --wait`; let the CLI poll until publication and source-Issue
  cleanup reach a terminal outcome instead of sleeping or manually polling.
- After a batch submission, use the CLI's batch status command. The trusted queue may merge the
  selected PRs sequentially in one runner and publish once afterward, but completion still requires
  checking every PR and every source Issue.
- Treat promotion and publication as separate stages. Report shared run IDs once, follow the CLI's
  latest-stage and `next` fields, and do not infer publication from PR merge alone.
- When the request covered every open contribution, run `review queue` after terminal publication
  and report whether the open moderation queue is empty.

When a command fails, follow its structured `error.next` guidance. Retry a read-only command when the
CLI marks a transient GitHub failure retryable; never replay a mutating command unless the CLI's
status confirms that doing so is safe. Do not bypass a guard by constructing a review comment outside
the CLI.
