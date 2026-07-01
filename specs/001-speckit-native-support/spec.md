# Feature Specification: Native Spec-Kit (`/speckit`) Mission Orchestration

**Feature Branch**: `001-speckit-native-support`

**Created**: 2026-06-28

**Status**: Draft

**Input**: User description: "Implement support for [spec-kit](https://github.com/github/spec-kit) natively from Kōan. An operator sends a `/speckit` signal from the chat app (`/speckit <project-id> do something`), via an issue-tracker URL (`/speckit <GITHUB-or-JIRA-ISSUE-URL>`, optionally `repo:myrepo branch:mybranch`), or by @mentioning the bot on an issue thread (`@bot /speckit` / `@bot speckit`). For issue triggers, the issue description followed by all messages becomes the problem to solve (same as `/fix` / `/implement`). If the target project has no `.specify/memory/constitution.md`, Kōan aborts early with a clear error; otherwise it assumes speckit is usable and orchestrates: (1) specify, (2) plan, (3) tasks, (4) implement, (5) a private review with a configurable fix loop, (6) test/CI validation with fix attempts, (7) a nice draft PR. Steps 5 and 6 must not abort on failure; if any of steps 1–4 fails, abort with a clear message."

## Clarifications

### Session 2026-06-28

- Q: How should `/speckit` interact with Kōan's quota and autonomous-mode machinery across its long (up to 7-step) pipeline? → A: Add an **early affordability gate**. Before starting a `/speckit` run, check remaining quota; if it is below a configurable threshold (default **15%** of session quota), do NOT start the run and do NOT abort it — place the mission **on hold** (paused) to await quota reset. When quota recovers above the threshold, the held mission proceeds automatically. This gate sits on top of the normal autonomous-mode machinery; a run that has already started continues to ride the normal mid-mission pause/resume behavior.
- Q: What observable execution/progress model should `/speckit` present? → A: **Single mission + per-step progress notes.** One queued mission entry in `missions.md`; the operator receives incremental progress notes (via the originating channel — chat outbox or the GitHub/Jira thread) as each pipeline step completes, and a single final result/abort. Aborting at steps 1–4 fails that one mission with a step-specific reason. Matches `/implement`. (The internal sequencing mechanism remains a plan-phase decision.)
- Q: If a `/speckit` mission is submitted for an issue/project that already has an in-progress speckit run or an open draft PR, what happens? → A: Multiple **distinct** `/speckit` missions are normal — they queue and run one at a time (Kōan's standard queue discipline), each landing in its own prefixed branch and draft PR, exactly like `/fix`, `/implement`, or any regular mission. No special per-target serialization is introduced. Dedup (skip) applies only to the **same** issue that already has an open PR, matching `/implement`.
- Amendment: Add a fourth trigger surface — `/speckit_from_branch <repo-id> <branch-name>` — for starting a run from a spec a human has **already authored, validated, and pushed** to a branch. This entry point **skips `/speckit.specify`** and runs the pipeline directly from `/speckit.plan` onward (plan → tasks → implement → review → CI → PR), following the exact same subsequent plan and constitution gate as the other triggers.
- Amendment: During `/speckit.implement`, add a **per-phase commit hint** so the agent commits at the end of each phase (incremental checkpointing), rather than producing a single monolithic commit at the end.

### Session 2026-06-29

- Q: For `/speckit_from_branch`, how should Kōan base the implementation branch and open the draft PR? → A: **New prefixed (`koan/*`) branch based off the human's `branch-name`.** The validated spec is inherited automatically, the always-prefixed-branch rule (FR-014) is honored, and the draft PR opens from the new branch. This matches the standard `/speckit` flow with the human's branch as the base instead of main, and keeps human vs. bot commits on separate branches.
- Q: For the per-phase commit hint (FR-019/SC-010), what should "phase" map to as the commit unit? → A: **Per task** — one commit after each `tasks.md` task completes (the unit speckit's implement loop processes). Each commit maps 1:1 to a task in the checklist, giving the finest individually-reviewable granularity.

## User Scenarios & Testing *(mandatory)*

<!--
  Prioritized as user journeys. Each story is independently testable and delivers
  value on its own. The spec fixes only the OBSERVABLE contract (triggers, gate,
  abort rules, final artifact) — the orchestration mechanism is a plan-phase
  decision (see Assumptions).
-->

### User Story 1 - Operator triggers speckit from chat with a free-text goal (Priority: P1)

An operator sends `/speckit <project> <goal>` from the chat bridge (e.g. Telegram).
Kōan resolves the target project, verifies it has a spec-kit constitution, and
runs the full spec-driven development (SDD) cycle — **specify → plan → tasks →
implement** — followed by a private review-fix loop, test/CI validation, and a
draft PR. The operator receives progress updates and a final PR link.

**Why this priority**: This is the primary, highest-value entry point — turning a
one-line operator intent into a fully specced, implemented, draft-PR feature with
no manual orchestration.

**Independent Test**: Send `/speckit myproject add a CSV export button` against a
constitution-backed project; assert a draft PR opens in `myproject` containing the
speckit artifacts (spec/plan/tasks) plus the implementation, and the operator
received a reply with the PR link.

**Acceptance Scenarios**:

1. **Given** a configured project that HAS a constitution, **When** the operator
   sends `/speckit myproject add CSV export`, **Then** Kōan runs the SDD cycle and
   opens a draft PR, replying with the link.
2. **Given** a configured project WITHOUT a constitution, **When** the operator
   sends `/speckit myproject ...`, **Then** Kōan replies with a clear, actionable
   error naming the missing file and project, and runs zero speckit steps.
3. **Given** a valid mission, **When** step 1 (specify), 2 (plan), 3 (tasks), or 4
   (implement) fails, **Then** Kōan aborts the whole mission and replies with a
   message identifying which step failed and why.

---

### User Story 2 - Operator triggers speckit from an issue-tracker URL (Priority: P1)

An operator sends `/speckit <github-or-jira-issue-url>`, optionally with
`repo:<repo>` / `branch:<branch>` overrides (the same syntax `/fix` and
`/implement` already honor). Kōan resolves the project from the issue's
repository, fetches the issue, and uses the **issue description followed by all
thread comments** as the "problem to solve" — exactly like `/implement`. The
constitution gate and the full pipeline then apply unchanged.

**Why this priority**: Issue-driven work is the dominant workflow for
`/implement`/`/fix`; speckit must support the identical trigger surface and
data-assembly behavior.

**Independent Test**: Send `/speckit https://github.com/owner/repo/issues/42`;
assert the speckit pipeline runs with the issue's title + body + comments as the
goal text and a draft PR opens against that repository.

**Acceptance Scenarios**:

1. **Given** a public GitHub issue whose repo is a configured project WITH a
   constitution, **When** `/speckit <issue-url>` is sent, **Then** the issue body
   + comments become the speckit goal and a draft PR opens.
2. **Given** a Jira issue key, **When** `/speckit PROJ-123` is sent, **Then** the
   Jira description + comments become the goal and the pipeline runs.
3. **Given** `repo:myrepo branch:mybranch` tokens appended, **When** the command
   runs, **Then** the implementation targets the specified repo and branch, and
   the tokens are stripped from the goal text.

---

### User Story 3 - Reviewer triggers speckit by @mentioning the bot on an issue thread (Priority: P2)

A human pings `@bot /speckit` (or the bare `@bot speckit`) on a GitHub/Jira issue
thread. Kōan treats the issue description + all comments as the problem, performs
its existing permission check, reacts to acknowledge, and runs the full pipeline —
the same `@mention` surface `/implement` and `/fix` already use.

**Why this priority**: It reuses the established `@mention` pipeline and enables
team workflows, but it is secondary to the direct chat/URL triggers.

**Independent Test**: Comment `@koan-bot /speckit` on an issue in a
constitution-backed project; assert Kōan reacts, runs the pipeline, and the
resulting draft PR links back to the issue.

**Acceptance Scenarios**:

1. **Given** an authorized user @mentions `/speckit` on an issue in a
   constitution-backed project, **Then** Kōan acknowledges and runs the pipeline.
2. **Given** an unauthorized user @mentions `/speckit`, **Then** Kōan ignores or
   declines per the existing permission rules — identical to other `@mention`
   skills.

---

### User Story 4 - Best-effort review and CI never block delivery (Priority: P2)

After implementation, Kōan runs a **private review** and a **test/CI validation**,
each attempting fixes up to a configurable number of iterations. If either cannot
fully resolve its findings, the mission still completes by opening a draft PR that
**surfaces the unresolved findings**, rather than aborting.

**Why this priority**: A draft PR is human-gated anyway; after implementation has
succeeded, surfacing unresolved issues for human review is strictly more useful
than silently discarding the work.

**Independent Test**: Force the review step to report an unfixable issue; assert
the mission still opens a draft PR and the PR body lists the unresolved finding
and which step produced it.

**Acceptance Scenarios**:

1. **Given** implementation succeeded, **When** the private review finds issues it
   cannot fix within its iteration budget, **Then** a draft PR is still opened with
   the unresolved findings noted in the PR body.
2. **Given** tests/CI fail and cannot be fixed within the CI-fix budget, **Then**
   a draft PR is still opened with the failure summarized.

---

### User Story 5 - Operator resumes from a human-validated spec on a branch (Priority: P2)

An operator sends `/speckit_from_branch <repo-id> <branch-name>`. The spec on that
branch was already authored and validated by a human and pushed, so Kōan **skips
`specify`** and runs the pipeline directly from `plan` (plan → tasks → implement →
review → CI → draft PR), following the same plan and constitution gate as the other
triggers.

**Why this priority**: It lets a human do the high-judgment specification work
themselves and hand off only the mechanical plan→implement→PR execution to Kōan — a
useful collaboration mode, but secondary to the fully-autonomous chat/URL triggers.

**Independent Test**: Push a validated `spec.md` to a branch in a
constitution-backed project; send `/speckit_from_branch myproject my-spec-branch`;
assert `specify` is NOT run, `plan` consumes the existing `spec.md`, and a draft PR
opens bundling the plan/tasks artifacts + implementation.

**Acceptance Scenarios**:

1. **Given** a branch in a constitution-backed project that contains a validated
   `spec.md`, **When** `/speckit_from_branch <repo-id> <branch-name>` is sent,
   **Then** Kōan skips `specify`, runs `plan` against the existing spec, and opens a
   draft PR.
2. **Given** the named branch contains NO usable `spec.md`, **When** the command
   runs, **Then** Kōan aborts at the `plan` step with a clear message that no spec
   was found on the branch.
3. **Given** the project lacks a constitution, **When** `/speckit_from_branch …` is
   sent, **Then** Kōan aborts early with the constitution error — the gate is not
   bypassed by this entry point.

---

### Edge Cases

- Target project has a constitution but the speckit commands are unavailable or
  `specify` errors → treated as a **step-1 abort** with a clear message.
- Constitution exists in Kōan's own repo but NOT in the target project → **abort**
  (the gate is evaluated against the *target* project, never Kōan itself).
- Operator sends `/speckit` with neither a project name nor an issue URL → reply
  with usage guidance; queue nothing.
- The URL points to a **PR** rather than an issue → behavior matches `/implement`'s
  URL classification (PR-shaped URLs are not valid specify inputs here).
- Pipeline produces `spec.md` / `plan.md` / `tasks.md` but the implement step
  yields no code change → **step-4 abort**.
- `/speckit_from_branch` names a branch with no usable `spec.md` → abort at `plan`
  (specify was intentionally skipped, so the missing spec surfaces there) with a
  clear message.
- A task within `implement` produces no changes → the per-task commit hint skips
  committing that task (no empty commits), but the pipeline continues.
- Multiple **distinct** `/speckit` missions (different goals/projects/issues) queue
  normally and run one at a time, each landing in its own prefixed branch and draft
  PR — exactly like `/fix`, `/implement`, or any regular mission. No special
  serialization logic is required; this is the standard mission-queue model.
- The **same** issue already has an open draft PR from a prior `/speckit` → skip to
  avoid duplicate work, consistent with `/implement`.
- Mission is interrupted/restarted mid-pipeline → partial speckit artifacts are
  preserved for inspection; recovery does not silently delete them.
- Very long issue threads → the assembled goal text (description + comments) is
  bounded/capped to fit context, the same way `/implement` bounds issue context.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Kōan MUST accept `/speckit` from four trigger surfaces: (a) chat
  `/speckit <project> <goal>`; (b) `/speckit <issue-url> [repo:<repo> branch:<branch>]`;
  (c) an `@bot /speckit` mention on a GitHub/Jira issue thread; (d)
  `/speckit_from_branch <repo-id> <branch-name>` to resume from a human-validated spec
  already pushed to a branch (see FR-020).
- **FR-002**: Kōan MUST resolve the target project by reusing the existing
  project-resolution path (explicit project arg → issue's repository → missions
  project tag).
- **FR-003**: Before running any speckit step, Kōan MUST verify the **target
  project** contains a spec-kit constitution at `.specify/memory/constitution.md`.
  If absent, Kōan MUST abort early and reply with a clear, actionable error that
  names both the missing file and the project — and MUST run zero speckit steps.
- **FR-004**: When the constitution is present, Kōan MUST treat the project as
  speckit-ready and proceed.
- **FR-005**: Kōan MUST assemble the "problem to solve" as: the operator's goal
  text for chat triggers; and for issue triggers, the issue title + description
  followed by all thread comments (the same data the `/implement`/`/fix` issue
  pipeline already gathers).
- **FR-006**: Kōan MUST run the speckit pipeline in order: **specify → plan →
  tasks → implement**. The `/speckit_from_branch` entry point skips `specify` (the
  spec is human-authored and already on the named branch) and starts at `plan`; the
  remaining steps and their ordering are unchanged.
- **FR-007**: Kōan MUST support optional `repo:<repo>` and `branch:<branch>`
  override tokens, parsing and stripping them from the goal text, reusing the
  existing token syntax already honored by `/fix` and `/implement`.
- **FR-008**: If any of steps specify/plan/tasks/implement fails — defined as the
  step's speckit command erroring OR failing to produce its expected artifact
  (`spec.md`, `plan.md`, `tasks.md`, or implemented code respectively) — Kōan MUST
  abort the entire mission and reply with a clear message identifying the failing
  step and the reason. Partial speckit artifacts produced before the failure MUST
  be preserved, not deleted.
- **FR-009**: After implementation, Kōan MUST run a **private review** using Kōan's
  existing review machinery and preferences, looping up to a configurable maximum
  number of iterations (default 3) to address issues at or above a configurable
  severity threshold ("important").
- **FR-010**: After the review loop, Kōan MUST validate tests/CI and attempt fixes
  up to a configurable iteration budget, reusing the existing CI-validation and
  CI-fix-loop mechanisms.
- **FR-011**: Failures or unresolved findings in the review (step 5) or CI
  validation (step 6) MUST NOT abort the mission.
- **FR-012**: At the end, Kōan MUST open a **draft** pull request in the target
  project that bundles the speckit artifacts (spec/plan/tasks) together with the
  implementation, and MUST reply with the PR link.
- **FR-013**: When review/CI leave unresolved findings, the draft PR body MUST
  summarize them (which step, what finding) so a human reviewer can see them.
- **FR-014**: Kōan MUST never merge the PR, MUST never commit to the project's
  default branch, and MUST land all work on a prefixed feature branch — honoring
  Kōan's Human-Authority invariant.
- **FR-015**: All inbound mission/issue/comment content MUST be treated as
  untrusted data (subject to the existing prompt-injection guard), and all
  outbound PR/commit content MUST pass through the existing outbound scanner.
- **FR-016**: `/speckit` MUST be discoverable via `/help` (assigned to a help
  group) and exposed to GitHub/Jira `@mentions` through the standard skill flags,
  consistent with every other core skill.
- **FR-017**: Before starting a `/speckit` run, Kōan MUST check remaining quota
  against a configurable affordability threshold (default **15%** of session
  quota). If remaining quota is below the threshold, Kōan MUST place the mission
  **on hold** (paused, awaiting quota reset) — it MUST NOT start the run and MUST
  NOT mark the mission failed/aborted. When quota recovers above the threshold,
  the held mission proceeds. A run that has already started continues to ride the
  normal mid-mission pause/resume behavior.
- **FR-018**: A `/speckit` invocation MUST manifest as a **single mission** in
  `missions.md`. Kōan MUST emit incremental progress notes to the originating
  channel (chat outbox, or the GitHub/Jira thread) as each pipeline step
  completes, and a single final result. An abort at steps 1–4 fails that one
  mission with a step-specific reason; it MUST NOT spawn one mission per step.
- **FR-019**: During the implement step, Kōan MUST apply a **per-task commit hint**
  instructing the agent to create one commit after each `tasks.md` task completes
  (so progress is checkpointed incrementally and each task is individually
  reviewable, with a 1:1 mapping between commits and the task list), rather than a
  single monolithic commit at the end. The commit cadence MUST follow the project's
  existing commit conventions.
- **FR-020**: For the `/speckit_from_branch <repo-id> <branch-name>` trigger, Kōan
  MUST resolve the target project from `repo-id`, treat the validated `spec.md`
  already present on `branch-name` as the input, **skip `specify`**, and run
  `plan → tasks → implement → review → CI → PR` exactly as for the other triggers.
  Kōan MUST create a new prefixed (`koan/*`) implementation branch **based off
  `branch-name`** (inheriting the validated spec) and open the draft PR from it —
  never committing directly onto the human's branch. The constitution gate (FR-003)
  still applies. For this variant, the hard-abort rule (FR-008) covers the steps
  that actually run — plan/tasks/implement — while review (step 5) and CI (step 6)
  remain best-effort.

### Key Entities *(include if feature involves data)*

- **Speckit Mission**: A `/speckit` invocation carrying a trigger surface, a
  resolved target project, an optional issue reference, optional `repo:`/`branch:`
  overrides, and the assembled goal text. Lifecycle: gated → specified → planned →
  tasked → implemented → reviewed → CI-checked → PR-opened (or aborted at steps
  1–4).
- **Constitution Gate**: The presence of `.specify/memory/constitution.md` in the
  target project — the single readiness signal that authorizes speckit execution.
- **Speckit Artifacts**: The `spec.md`, `plan.md`, and `tasks.md` (alongside the
  constitution) produced under the target project's spec-kit layout; shipped inside
  the final draft PR.
- **Pipeline Step Outcome**: Per-step success/failure status that drives the
  sequential gating — hard abort on steps 1–4, best-effort continuation on 5–6.
- **Entry Mode / Spec Source**: Whether the spec is agent-authored (`specify` runs)
  or human-supplied (`/speckit_from_branch` skips `specify` and consumes an existing
  `spec.md` on the named branch). Entry mode changes only the first pipeline step;
  everything downstream is identical.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of `/speckit` missions targeting a constitution-backed project
  either complete their full gated pipeline (specify→plan→tasks→implement for the
  standard triggers; plan→tasks→implement for `/speckit_from_branch`) and open a
  draft PR, or abort at a gated step with a step-specific message — never a silent
  no-op.
- **SC-002**: 100% of `/speckit` missions targeting a project WITHOUT a
  constitution abort before any speckit step runs, with a clear, actionable error
  naming the missing file and project.
- **SC-003**: 0% of `/speckit` missions abort solely because the review (step 5)
  or CI validation (step 6) failed — these steps are best-effort by construction.
- **SC-004**: From the operator's final reply alone, the operator can determine
  whether the mission succeeded (PR link), aborted (which step + why), or
  completed with unresolved findings (PR link + summary).
- **SC-005**: For typical missions, the private review-fix loop resolves the
  majority of "important" findings before the PR is opened (a qualitative quality
  bar bounded by the configurable iteration budget).
- **SC-006**: A human reviewer receiving the draft PR sees both the implementation
  and the speckit design artifacts (spec/plan/tasks) in one reviewable unit, plus
  any unresolved review/CI notes.
- **SC-007**: Triggering `/speckit` from chat, from an issue URL, and via
  `@mention` all converge on the identical pipeline and constitution gate.
- **SC-008**: 100% of `/speckit` missions submitted while remaining quota is below
  the configurable threshold (default 15%) are placed **on hold** — not started,
  not failed — and proceed automatically once quota recovers; none are aborted
  solely for low quota.
- **SC-009**: 100% of `/speckit_from_branch` runs skip `specify` and complete
  `plan → tasks → implement → review → CI → PR` against the human-validated spec on
  the named branch, applying the same constitution gate and abort rules as the other
  triggers.
- **SC-010**: For every completed `implement` step spanning more than one task, the
  resulting branch history contains **at least one commit per `tasks.md` task** (no
  monolithic single-commit implementation), each following the project's commit
  conventions.

## Assumptions

- The constitution gate is the **sole** readiness check. Kōan does NOT
  auto-initialize spec-kit (`speckit init` / scaffold templates) for a project. If
  the constitution is present, speckit is assumed usable; a missing or erroring
  `specify` command surfaces naturally as a step-1 abort.
- **"Private review"** reuses Kōan's existing review code and preferences but runs
  internally — findings drive the fix loop and are summarized in the draft PR,
  rather than being posted as standalone public review comments mid-pipeline.
- Default review-fix iteration budget is **3** (configurable); the "important"
  severity threshold is configurable. The CI-fix iteration budget defaults to the
  existing CI fix loop's default.
- **Quota affordability gate**: a `/speckit` run requires a configurable minimum
  remaining quota (default **15%** of session quota) to START; below that, the
  mission is held (paused) for quota reset rather than started or aborted. This is
  a start-gate only — a run already in progress rides the normal mid-mission
  pause/resume behavior.
- The final PR is **always a draft** on a prefixed feature branch, opened in the
  **target project** (never Kōan's own repo), bundling speckit artifacts +
  implementation.
- `/speckit` **reuses existing mechanisms** rather than introducing parallel ones:
  project resolution, `repo:`/`branch:` token parsing, issue-thread context
  fetching, CI validation/fix loop, draft-PR creation, the prompt-injection guard,
  and the outbound scanner. (Constitution-principle VII — simplicity.)
- **Queueing model**: multiple `/speckit` missions may be queued and run one at a
  time, each independent (own branch, own draft PR), exactly like `/fix` /
  `/implement`. No per-target serialization feature is introduced; the standard
  mission queue handles it. Dedup (skip) applies only to the identical issue that
  already has an open PR, matching `/implement`.
- Bare-word `speckit` (no slash) via `@mention` is accepted as an alias form; the
  canonical command is `/speckit`.
- **`/speckit_from_branch`** trusts the human-validated `spec.md` already on the
  named branch: Kōan does NOT re-run `specify`, does NOT regenerate or overwrite the
  spec, and consumes it as-is as the input to `plan`. `repo-id` resolves via the
  existing project-resolution path; `branch-name` is the branch the spec lives on
  and the base a new prefixed `koan/*` implementation branch is created from (the
  human's branch itself is never committed to directly). Whether this surface is a
  separate skill or an alias/mode of `/speckit` is a plan-phase decision.
- **Per-task commit**: the implement commit unit is one `tasks.md` task — the agent
  commits after each task completes (1:1 with the task list), with no empty commits
  when a task produces no changes. Finer/coarser cadence is plan-phase.
- Inbound issue/comment text is untrusted data; very long threads are capped to fit
  context, consistent with how `/implement` bounds issue context.
- **Specs-directory layout (open TODO `SPECS_DIR_COLLISION`)**: this feature's
  speckit trio lives at `specs/001-speckit-native-support/` (the spec-kit default),
  coexisting as a sibling of Kōan's design-contract directories `specs/components/`
  and `specs/skills/` (no filesystem collision for a single feature). The durable
  design contract for the resulting `/speckit` skill will additionally be recorded
  in `specs/skills/speckit.md`. Long-term separation of one-shot feature specs from
  design contracts is **deferred to the plan phase** — this assumption defers the
  constitution's open `SPECS_DIR_COLLISION` TODO rather than silently resolving it.
- The **observable execution model** is fixed: a `/speckit` invocation is a single
  mission in `missions.md` with per-step progress notes and a single final
  result/abort (matches `/implement`). The **internal sequencing mechanism** (e.g.
  one agent mission that performs all steps vs. an internal orchestrator that
  drives sub-steps while presenting as one mission) remains a plan-phase decision.
  Either way, the observable contract holds: sequential gating with hard abort on
  steps 1–4 and best-effort continuation on steps 5–6.
