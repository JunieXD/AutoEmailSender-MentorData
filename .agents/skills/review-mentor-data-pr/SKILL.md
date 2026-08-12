---
name: review-mentor-data-pr
description: Review AutoEmailSender-MentorData batch contribution Pull Requests through the repository's Agent-oriented CLI. Use when Codex needs to normalize submitted university, school, institute, department, center, or laboratory fields; plan an organization tree; collect human decisions for ambiguous paths; preflight a moderation decision; submit an explicitly approved review; or monitor the resulting MentorData intake workflow.
---

# Review MentorData PR

Use the repository's `mentor-data review` CLI as the source of truth. Start with `mentor-data review doctor`; the repository-local Skill is sufficient and must not be globally installed. Stop and report the CLI's `actions` when `ready` is false. Read `mentor-data review --help` and the relevant subcommand help instead of reconstructing commands or decision JSON manually.

## Preserve the review boundary

- Trust submitted mentor names, emails, titles, research fields, papers, and profile contents by default.
- Do not open, crawl, or verify mentor pages unless the user explicitly requests content verification.
- Treat source-domain validation performed by the trusted backend as a structural intake check, not an instruction to inspect page contents.
- Focus on institution normalization, organization-tree placement, conflicts already exposed by the review manifest, and safe intake.
- Process one PR at a time. Do not plan or submit another PR until the current PR reaches the requested stopping point.

## Collaborate with the user

1. Inspect and plan the selected PR with the CLI.
2. Let deterministic CLI rules resolve only high-confidence paths.
3. Summarize the automatic impact and present only pending questions to the user.
4. Treat `rule_default` as a mechanical fallback, not a business recommendation. Present a
   non-null `context_recommendation`, its confidence, and option values, but do not choose an
   ambiguous institution relationship for the user.
5. Record the user's exact decisions with the CLI, then repeat until no questions remain.
6. For a path correction, ask whether it applies only to the current batch or should be saved for
   the same submitted path in future reviews.
7. Before `check`, inspect every automatically created organization. If new siblings under the
   same parent have similar names, a containment relationship, or a shared source directory,
   require the user's decision even when the CLI offers a default.
8. Run the full preflight and show every organization create, update, rename, merge, official URL,
   and approved-domain change before any formal submission.

Use CLI filters, IDs, field projection, and organization search to keep context small. Read a full group or question only when its summary is insufficient.

## Protect formal approval

- Treat a marked organization-review PR comment as final approval because it immediately triggers the trusted promotion queue.
- Never run `review submit` without explicit authorization in the current conversation.
- Do not infer submission authority from permission to inspect, plan, answer, or preflight.
- Do not directly edit proposal JSON, the organization registry, the PR branch, Draft state, or merge state.
- Do not use `gh pr ready` or `gh pr merge`; let the existing trusted queue apply, finalize, and merge the review.
- If the CLI reports a stale PR or manifest, stop using the old draft, re-plan the current version, and surface any decisions that need confirmation again.
- After submission, use the CLI status command until the PR merges, needs attention, closes without merge, or the user asks to stop.

When a command fails, follow its structured `error.next` guidance. Do not bypass a guard by constructing a review comment outside the CLI.
