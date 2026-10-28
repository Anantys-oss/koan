# Feature Specification: Config-driven mission hooks (`.koan/config.yaml` pre/post hooks)

**Feature Branch**: `koan.atoomic/config-mission-hooks`

**Created**: 2026-07-26

**Status**: Draft

**Input**: User description: "add pre_hooks/post_hooks to the repo-level `.koan/config.yaml`: shell commands run before and after a mission, keyed by mission type, with a `default` fallback; support all main missions (review, fix, plan, rebase, refactor, implement); post_hooks run on success or failure with a status env var; commands run in order."

## Overview

The repo-level `.koan/config.yaml` (introduced with `review.always_check`) is a
project owner's steering surface, read from the **target repository** Kōan works
on — distinct from the operator's `KOAN_ROOT` `instance/config.yaml`. This feature
extends that surface with **mission hooks**: lists of shell commands the repo owner
wants Kōan to run **before** a mission (`pre_hooks`) and **after** it finishes
(`post_hooks`), so a repo can prepare its environment (install deps, start a
service, warm a cache) and tear it down or report status (stop the service, emit a
notification) around any mission Kōan runs on it.

Because these commands are arbitrary shell defined by a file *inside the target
repo*, they are a new remote-code-execution surface on the operator's host and are
therefore **disabled by default** and gated behind an explicit operator opt-in.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Per-mission-type setup and teardown (Priority: P1)

A repo owner whose review needs a running database adds, in their repo's
`.koan/config.yaml`, a `review.pre_hooks` list that starts the database and a
`review.post_hooks` list that stops it. The operator has enabled mission hooks for
that project. When Kōan runs a `/review` mission on the repo, it runs the
`review.pre_hooks` commands (in order) before the review and the
`review.post_hooks` commands (in order) after it — regardless of whether the review
succeeded or failed.

**Why this priority**: This is the core capability and the motivating use case; it
delivers value on its own even without the `default` fallback or the full
mission-type matrix.

**Independent Test**: Configure `review.pre_hooks: ["touch pre.flag"]` and
`review.post_hooks: ["touch post.flag"]`, enable hooks for the project, run a review
mission, and confirm both flag files exist and were created before/after the review
body respectively.

**Acceptance Scenarios**:

1. **Given** hooks enabled and a repo `.koan/config.yaml` with `review.pre_hooks`
   and `review.post_hooks`, **When** a review mission runs, **Then** each pre-hook
   command runs (in listed order) before the review begins and each post-hook
   command runs (in listed order) after it ends.
2. **Given** a `review.post_hooks` command that inspects `$KOAN_MISSION_STATUS`,
   **When** the review mission fails, **Then** the post-hook observes
   `KOAN_MISSION_STATUS=failure`; **When** it succeeds, **Then** the post-hook
   observes `KOAN_MISSION_STATUS=success`.
3. **Given** a `pre_hooks` list of three commands where the second exits non-zero,
   **When** the mission runs, **Then** the failure is logged and the mission
   proceeds (hooks are best-effort and never abort the mission).

### User Story 2 - `default` hooks applied to every mission (Priority: P2)

A repo owner wants the same setup to run before *every* mission Kōan performs,
regardless of type. They set `default.pre_hooks` (and optionally
`default.post_hooks`). Any mission type without its own section inherits the
`default` list; a mission type *with* its own section uses that section instead.

**Why this priority**: Reduces duplication for the common "always run repo setup"
case and generalizes the feature beyond review, but depends on the P1 execution
machinery.

**Independent Test**: Set only `default.pre_hooks: ["echo default"]`, run a `plan`
mission (which has no `plan.pre_hooks`), and confirm the default pre-hook ran.

**Acceptance Scenarios**:

1. **Given** `default.pre_hooks` set and no `plan.pre_hooks`, **When** a plan
   mission runs, **Then** the `default.pre_hooks` commands run.
2. **Given** both `default.pre_hooks` and `review.pre_hooks` set, **When** a review
   mission runs, **Then** only `review.pre_hooks` run (the mission-type section
   fully replaces the default for that phase — the two lists are not merged or
   concatenated).
3. **Given** both `default.pre_hooks` and `review.pre_hooks` set, **When** a review
   mission runs and `review.post_hooks` is absent but `default.post_hooks` is
   present, **Then** the pre-phase uses `review.pre_hooks` and the post-phase falls
   back to `default.post_hooks` (precedence is resolved per phase independently).

### User Story 3 - Operator safety gate (Priority: P1)

An operator running Kōan across many projects must not have arbitrary shell from a
third-party repo's `.koan/config.yaml` execute on their host by default. Mission
hooks only run when the operator has explicitly opted in; otherwise any
`pre_hooks`/`post_hooks` present in a repo config are ignored, with a single
diagnostic logged so the operator can see they were skipped.

**Why this priority**: This is a security-critical invariant. Shipping the execution
capability without the default-off gate would be a regression in the operator's
security posture, so it is as critical as the core capability itself.

**Independent Test**: With the opt-in **absent** (default), configure repo
`review.pre_hooks: ["touch should-not-exist.flag"]`, run a review mission, and
confirm the flag file was **not** created and a "hooks skipped (not enabled)"
diagnostic was logged.

**Acceptance Scenarios**:

1. **Given** the operator opt-in is absent/false, **When** any mission runs on a
   repo that defines `pre_hooks`/`post_hooks`, **Then** no hook command executes and
   one diagnostic line is logged noting hooks were skipped because they are not
   enabled.
2. **Given** the operator opt-in is enabled globally but disabled for a specific
   project, **When** a mission runs on that project, **Then** no hook command
   executes for that project.
3. **Given** the operator opt-in is enabled, **When** a mission runs, **Then**
   configured hooks execute per User Stories 1 and 2.

### Edge Cases

- **Absent / empty / malformed config**: no `.koan/config.yaml`, an empty file, a
  file that is not a YAML mapping, or a `pre_hooks`/`post_hooks` value that is not a
  list of strings → treated as "no hooks" for that section; the mission runs
  unchanged and no hook executes. A malformed shape logs at most one diagnostic and
  never raises.
- **Non-string / blank command entries**: entries that are not strings, or are
  blank/whitespace-only, are dropped from the list; remaining valid commands run.
- **Command timeout**: a hook command that exceeds the per-command timeout is
  terminated, logged as timed out, and the remaining commands / the mission proceed.
- **Unknown mission type**: a repo may define a section for a mission type that does
  not map to a real mission (e.g. a typo). It simply never matches and is inert.
- **Mission with no resolvable type** (a plain autonomous mission that is not a
  slash-command skill): only `default` hooks apply; type-specific sections do not.
- **Very long or very large hook lists**: the number of commands per list and the
  length of each command are hard-capped; excess is dropped with a diagnostic so a
  pathological config cannot hang or flood the host.
- **Hook count / output flooding**: hook stdout/stderr captured into the run log is
  bounded so a chatty command cannot exhaust log storage.
- **Both execution paths**: the same behavior applies whether the mission is
  skill-dispatched (e.g. review, fix, plan, rebase, implement) or runs through the
  autonomous agent loop (e.g. refactor / free-form missions).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST read an optional `pre_hooks` and `post_hooks` list of
  shell commands from a target repository's `.koan/config.yaml`, under a
  mission-type section (e.g. `review:`, `fix:`, `plan:`, `rebase:`, `refactor:`,
  `implement:`) and under a `default:` section.
- **FR-002**: Before a mission runs, the system MUST resolve the applicable
  `pre_hooks` list and run each command in listed order in the target project's
  working directory.
- **FR-003**: After a mission finishes — on **both** success and failure — the
  system MUST resolve the applicable `post_hooks` list and run each command in
  listed order in the target project's working directory.
- **FR-004**: Precedence MUST be resolved **per phase** (pre vs post) and per
  mission type: if a section for the mission's type defines a list for that phase,
  that list is used and the `default` list for that phase is **not** also run
  (replace, not merge); otherwise the `default` list for that phase is used; if
  neither is present, no hook runs for that phase.
- **FR-005**: The mission type key MUST be derived from the mission's resolved
  command name so that the same key applies consistently across how the mission was
  triggered (Telegram, GitHub @mention, scheduled, requeued). `fix` MUST apply to
  fix missions whether the target is a PR or an issue.
- **FR-006**: Post-hook commands MUST be able to observe the mission outcome via an
  environment variable (`KOAN_MISSION_STATUS` set to `success` or `failure`) and the
  mission type via `KOAN_MISSION_TYPE`, so a single post-hook can branch on outcome.
- **FR-007**: Hook execution MUST be **best-effort / fire-and-forget**: a command
  that exits non-zero, cannot start, or times out MUST be logged but MUST NOT abort
  the mission, block subsequent hook commands, or crash the daemon.
- **FR-008**: Each hook command MUST run under a bounded per-command timeout; on
  timeout the command is terminated and the run continues.
- **FR-009**: Hook command stdout/stderr and lifecycle (start, exit code, duration,
  timeout) MUST be captured to the run log (visible via `make logs`), bounded so a
  noisy command cannot flood the log.
- **FR-010**: Absent, empty, unreadable, unparseable, or non-mapping
  `.koan/config.yaml`, and any `pre_hooks`/`post_hooks` value that is not a list of
  strings, MUST be treated as "no hooks" and MUST NOT raise or abort the mission
  (fail-safe, mirroring the existing `.koan/config.yaml` reader contract).
- **FR-011**: Individual command entries that are non-strings or blank MUST be
  dropped; the number of commands per list and the length of each command MUST be
  hard-capped, with excess dropped and one diagnostic logged.
- **FR-012**: Config-driven mission hooks MUST be **disabled by default** and run
  only when the operator has explicitly enabled them via a setting in the operator's
  `KOAN_ROOT` `instance/config.yaml` (following the existing `review_dispatch` /
  `ci_dispatch` `enabled: true` opt-in pattern). When disabled, no hook command runs
  and a single diagnostic notes that hooks were skipped because they are not enabled.
- **FR-013**: The operator MUST be able to disable mission hooks for a specific
  project even when the global opt-in is enabled (per-project override); a
  project-level disable takes precedence over the global enable for that project.
- **FR-014**: The feature MUST apply to both mission execution paths — skill-
  dispatched missions (e.g. review, fix, plan, rebase, implement, recreate, squash)
  and autonomous agent-loop missions (e.g. refactor and free-form missions).
- **FR-015**: The behavior MUST be a byte-for-byte no-op for existing users who do
  not enable the opt-in and/or do not add `pre_hooks`/`post_hooks` to any repo — no
  change to mission timing, output, or logs beyond the feature's own paths.
- **FR-016**: The feature MUST ship end-user documentation (the `.koan/config.yaml`
  docs page + the committed annotated sample config) and an operator-facing
  threat-model note describing the RCE surface, the default-off gate, and safe-use
  guidance.

### Key Entities

- **Mission hook list**: an ordered list of shell command strings for one
  (mission-type-or-default, phase) pair; phase ∈ {pre, post}.
- **Resolved hook plan**: for a given mission, the concrete `pre_hooks` and
  `post_hooks` lists after applying the mission-type-over-default precedence and the
  enablement gate (may be empty).
- **Hook execution result**: per command — exit status, duration, whether it timed
  out, and captured (bounded) output — used only for logging.
- **Operator hook gate**: the operator-controlled enablement state (global opt-in +
  optional per-project override) that decides whether any repo hook may run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the opt-in enabled and a repo defining `review.pre_hooks` /
  `review.post_hooks`, a review mission runs every configured command exactly once,
  in order, before/after the review body respectively.
- **SC-002**: With the opt-in **not** enabled, a repo's configured hooks run **zero**
  commands, and the operator can see (via `make logs`) a single line indicating hooks
  were skipped.
- **SC-003**: A post-hook can reliably distinguish a successful mission from a failed
  one 100% of the time via the status environment variable.
- **SC-004**: A pre-hook or post-hook that fails or hangs never causes a mission to
  abort or the daemon to crash — mission completion behavior is unchanged from the
  no-hooks baseline in every failure mode tested.
- **SC-005**: Existing users who do not adopt the feature observe no change in
  mission behavior, output, or performance (the feature is a verified no-op when the
  gate is off and/or no hooks are configured).
- **SC-006**: Given `default.pre_hooks` and a mission-type-specific `pre_hooks`, the
  mission-type list is the only pre-phase list that runs (no duplication from
  `default`), for both a type that overrides and a type that inherits.

## Assumptions

- The operator has explicitly added the target repositories to their `projects.yaml`
  and thus exercises some trust over them; the opt-in gate is the deliberate boundary
  that converts that ambient trust into permission to execute repo-defined shell.
- "Mission over" for post-hook purposes means Kōan's mission execution for that run
  has concluded (the runner/agent process has returned), before or independent of any
  auto-merge / notification post-processing; success/failure is taken from the
  mission's own completion status.
- Hooks run with the privileges of the Kōan agent process (same trust level as the
  existing Python lifecycle hooks); the security gate — not privilege dropping — is
  the control that makes this acceptable.
- A single shared per-command timeout and per-list/per-command caps are acceptable
  defaults; making them individually configurable is out of scope for v1 (documented
  as a possible future extension).
- Commands run via the system shell so operators can use pipelines/`&&`, consistent
  with the "shell/bash command" framing in the request.
- The `default` section name and the mission-type section names reuse the resolved
  skill command vocabulary already used elsewhere in Kōan; `refactor` and any
  non-skill mission resolve appropriately (type-specific only when a command name is
  resolvable, else `default`).

## Out of Scope (v1)

- Per-command timeout / cap overrides in the repo config.
- Merging/appending mission-type hooks onto `default` hooks (v1 is replace-only).
- Passing rich mission metadata to hooks beyond status and type env vars.
- A sandbox / privilege-drop for hook execution (mitigation is the opt-in gate +
  documentation, not isolation).
- Hooks for lifecycle events other than per-mission pre/post (session start/end are
  already served by the Python lifecycle-hook system).
