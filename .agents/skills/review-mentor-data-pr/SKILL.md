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
3. Summarize `path_normalizations` and automatic organization impact, then fetch pending decision
   packets together with `review questions --details`; present only genuine ambiguities to the user.
4. Treat `rule_default` as a mechanical fallback, not a business recommendation. Present a
   non-null `context_recommendation`, its confidence, and option values, but do not choose an
   ambiguous institution relationship for the user.
5. Record the user's exact decisions with the CLI, then repeat until no questions remain.
6. Offer to save a future identical-path correction only when the question exposes
   `future-identical-path` in `path_correction_scopes`; otherwise describe the decision as
   current-batch only.
7. Before `check`, inspect every automatically created organization. Require the user's decision
   when the CLI reports similar or containing sibling names, or a shared non-index detail source.
   A common directory or `list`/`index` page alone is not duplicate-institution evidence.
8. Run the full preflight and show every path normalization, organization create, update, rename,
   merge, official URL, and approved-domain change before any formal submission.

For a consolidated review, plan every PR first, resolve deterministic items, and present all
remaining ambiguities together. Group the summary by PR, then render each PR's institution changes
as its own nested tree. Never use one PR's preflight as evidence that another PR is safe.

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
- Do not infer submission authority from permission to inspect, plan, answer, or preflight.
- Do not directly edit proposal JSON, the organization registry, the PR branch, Draft state, or merge state.
- Do not use `gh pr ready` or `gh pr merge`; let the existing trusted queue apply, finalize, and merge the review.
- If the CLI reports a stale PR or manifest, stop using the old draft, re-plan the current version, and surface any decisions that need confirmation again.
- After submission, run `review status --wait`; let the CLI poll until publication and source-Issue
  cleanup reach a terminal outcome instead of sleeping or manually polling.
- After a batch submission, use the CLI's batch status command. The trusted queue may merge the
  selected PRs sequentially in one runner and publish once afterward, but completion still requires
  checking every PR and every source Issue.

When a command fails, follow its structured `error.next` guidance. Do not bypass a guard by constructing a review comment outside the CLI.
