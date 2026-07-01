# /speckit — native spec-kit orchestration

You are running Kōan's native spec-kit (`/speckit`) pipeline in the project
**{PROJECT}**.

**Branch discipline**: do all work on a `{BRANCH_PREFIX}*` branch based on
`{BASE_BRANCH}`. NEVER commit to `{BASE_BRANCH}` and NEVER merge — shipping is a
human decision. Open a **draft** PR at the end.

## Goal

{GOAL}

> If the goal above is an issue/bug URL, fetch and read it first; the issue
> description followed by all comments IS the problem to solve.

## Pipeline — run these in order

1. **specify** — run the spec-kit specify step for the goal; produce the feature
   `spec.md`.
2. **plan** — run the spec-kit plan step against the spec; produce `plan.md`.
3. **tasks** — run the spec-kit tasks step against the plan; produce `tasks.md`.
4. **implement** — run the spec-kit implement step, executing `tasks.md`.
   **Commit after every task** (one commit per task, following the project's
   commit conventions; skip empty commits for no-change tasks).

Invoke the spec-kit steps via their commands — `/speckit-specify`,
`/speckit-plan`, `/speckit-tasks`, `/speckit-implement` — or, if those skills are
unavailable in this project, the `specify` / `plan` / `tasks` / `implement` CLI.

### Hard abort (steps 1–4)

If **specify, plan, tasks, or implement** fails — the step errors, or its expected
artifact (`spec.md` / `plan.md` / `tasks.md` / implemented code) is not produced —
**STOP immediately**. Report which step failed and why. Do NOT continue to
review/CI/PR. Preserve any partial artifacts already produced.

## Best-effort — these NEVER abort

5. **Private review** — review your own implementation against the project's
   review conventions. Fix findings at **{REVIEW_SEVERITY}** severity or above.
   Repeat up to **{REVIEW_MAX_ITERATIONS}** times. Unresolved findings are NOT
   fatal.
6. **CI / tests** — run the project's tests and CI checks. If they fail, attempt
   to fix. A failure here is NOT fatal.

## Finish

7. Open a **draft** pull request targeting `{BASE_BRANCH}`, bundling the spec-kit
   artifacts (`spec.md`, `plan.md`, `tasks.md`) together with the implementation.
   If review or CI left **unresolved** findings, summarize them (which step, what
   finding) in the PR body so a human reviewer sees them.

Report the draft PR URL when done. If you aborted at steps 1–4, state that
clearly with the failing step and reason.
