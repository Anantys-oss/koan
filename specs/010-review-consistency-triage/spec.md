# Feature Specification: Review Consistency, Yellow-Tier Triage & Comprehensive Discovery

**Feature Branch**: `feat/review-accuracy-and-repo-context` (continuation)

**Created**: 2026-07-21

**Status**: Draft

**Input**: User description: "Starting from the current branch I would like to explore and improve user experience from code reviews triggered by /review. Several issues: (1) /review keeps finding different issues each time it's run and this is frustrating; (2) /review groups issues in red/yellow/green buckets — the yellow one for important issues needs better filtering so less critical issues are either ignored or bumped to a recommendation (green)."

**Follow-up input**: "Consider using sub-agents and reconciling findings to find *more* issues, so a future review has *less* delta. Add it as an extra prompt partial gated by a configuration option, disabled by default."

## Context (docs/specs consulted)

Grounded in the durable review contract `specs/skills/review.md` and the current
implementation:

- Findings carry three severities — `critical` (🔴 Blocking), `warning` (🟡
  Important), `suggestion` (🟢 recommendation). The merge verdict (`lgtm`) is
  `false` whenever **any** `critical` *or* `warning` finding exists; `suggestion`-only
  reviews are merge-ready. So today the **yellow tier is a blocking tier** — anything
  that lands there blocks the PR.
- A reflection pass already scores each finding 0–10 and drops those below a
  configurable threshold (`review_reflect.threshold`, default 5); calibration hints
  from `learnings.md` bias that scoring.
- The current branch already added prior-finding **reconciliation** (suppress
  already-fixed findings at HEAD), **snippet validation** (resync/drop stale quotes),
  and **repo-convention ingestion** (avoid convention-based false positives).

What the durable contract does **not** yet provide, and what this feature adds:
1. Any guarantee that re-running `/review` on the **same, unchanged** PR yields the
   same findings — findings are re-derived by the model each run and drift.
2. A crisp, testable **bar for the yellow tier** — the calibration is prose ("should
   be fixed but won't cause immediate harm"), so borderline "should-fix" items land in
   yellow and block merge.
3. Any way to make a single review **comprehensive**. Today's extra passes
   (silent-failure-hunter, architecture focus, bot-triage) run sequentially and are
   *appended* to an already-posted review rather than being merged and reconciled into
   one deduplicated finding set; `ThreadPoolExecutor` is used only to fetch context, not
   to run review passes in parallel. So a review can miss real issues on run 1 that a
   later run happens to catch — those "new" findings are a major source of the
   run-to-run delta described in item 1. Making each review more complete shrinks that
   delta at the source.

## Clarifications

### Session 2026-07-21

- Q: Finding identity — what attributes compose the key used to match a finding across runs (and dedup across discovery perspectives)? → A: Tolerant key — `file` + nearby code region (small line-window tolerance) + semantic issue topic/category. Not exact line numbers, not model wording.
- Q: Reuse key — what must be unchanged for the "reproduce prior review" short-circuit to fire? → A: Both the PR head SHA **and** the base (merge-base) SHA; any base movement or retarget forces a re-derive (with reconciliation).
- Q: Discovery perspectives — is the perspective set fixed or operator-configurable? → A: Fixed, defined set enumerated in the prompt partial (correctness, security, architecture, silent-failure, test-coverage); operators toggle the whole mode on/off, not individual lenses.
- Q: On a re-review, what happens to a *first-time* finding located in code unchanged since the prior review (the "review whiplash" case — new complaints on code the author never touched)? → A: Suppress it (freeze), **except** `critical`/real-harm findings, which still surface marked as *pre-existing*. The freeze is keyed on the incremental diff (prior reviewed SHA → current SHA); recurring prior findings and findings on newly-changed code are unaffected.
- Q: How does the new `[Pre-Existing Issue]` demote+label rule (Story 6) compose with the FR-003 freeze for a non-critical pre-existing issue *missed in round 1* and only caught on a later re-review? → A: Coexist — the freeze wins on re-review novelty (it stays suppressed); the `[Pre-Existing Issue]` demotion/label is the presentation rule for pre-existing findings that DO surface (round 1, changed-code findings, and `critical` exceptions).
- Q: Whose PR comments count as an authoritative disposition of a finding (Story 7)? → A: Any non-bot human commenter ("the human decides"); Kōan's own comments are excluded. Compensated by mandatory attribution/audit (FR-036) and an injection guardrail (FR-038).
- Q: Can a human dismissal suppress a `critical` finding (Story 7)? → A: Yes — full human authority applies to all severities, including `critical`; the suppression must be attributed and auditable, never silent.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consistent findings on repeated review (Priority: P1)

A maintainer runs `/review` on a PR, reads the findings, and later re-runs `/review`
on the **same PR with no new commits** — to double-check, to refresh the comment, or
because a teammate asked. Today the second review surfaces a partly different set of
issues: some findings vanish, new ones appear, and severities shift, even though not a
single line of code changed. This "review roulette" erodes trust: the maintainer can no
longer tell whether a finding is a real signal or a sampling artifact, and the PR author
gets whiplash from a moving target.

The feature makes a repeat review of unchanged code **reproduce the prior review**, and
makes a review after new commits **build on** the prior findings (recurring issues
recur; fixed ones are suppressed; only genuinely new issues are added) rather than
re-rolling from scratch.

**Why this priority**: This is the user's most-cited frustration and the trust
foundation of the whole feature. If findings aren't stable, no amount of severity tuning
matters — the maintainer distrusts the output either way. It delivers standalone value:
even with the current severity model, stable reviews are far more usable.

**Independent Test**: Run `/review` twice on the same PR with no push in between and
confirm the second posted review reproduces the first (same findings, same severities,
same verdict). Then push one commit that fixes one finding and confirm the third review
suppresses the fixed finding, retains the others unchanged, and adds only issues that the
new commit introduced.

**Acceptance Scenarios**:

1. **Given** a PR reviewed once with findings {F1, F2, F3} at HEAD `abc123`, **When**
   `/review` runs again with HEAD still `abc123` (no new commits), **Then** the posted
   review reproduces {F1, F2, F3} with the same severities and the same `lgtm` verdict —
   no findings dropped, added, or re-graded.
2. **Given** the same PR, **When** the author pushes a commit that fixes F1 and touches
   nothing else, **Then** the next review suppresses F1 as resolved, retains F2 and F3
   unchanged, and adds a first-time finding **only where the new commit changed code** —
   never a first-time `warning`/`suggestion` on code unchanged since the prior review (the
   FR-003 freeze; `critical` is the only exception, marked pre-existing).
3. **Given** a PR whose HEAD is unchanged since the last review, **When** `/review` is
   re-run, **Then** the maintainer can tell from the posted comment that this is a
   reproduction of the prior review (not a silent no-op and not a surprise re-roll).
4. **Given** a review re-run on unchanged code, **When** results are compared to the
   prior run, **Then** the blocking finding set (🔴 + 🟡) is identical, not merely
   "similar".
5. **Given** round 1 reported issues 1 & 2 at HEAD_A, **When** the author fixes them and
   pushes HEAD_B touching only those areas and `/review` re-runs, **Then** issues 1 & 2 are
   suppressed as resolved and **no first-time `warning`/`suggestion` finding is surfaced for
   code unchanged since HEAD_A** — a pre-existing issue in that code surfaces on this round
   only if it is `critical`, carrying the `[Pre-Existing Issue]` prefix. (This is the exact
   "finds new issues on already-existing code" frustration; the freeze eliminates it below
   `critical`.)

---

### User Story 2 - A trustworthy yellow (Important) tier (Priority: P1)

The maintainer relies on the color buckets to triage: red = must fix, yellow =
important, green = nice-to-have. Today the yellow bucket is over-inclusive — borderline
"you should probably fix this" items land in yellow, and because yellow blocks merge,
they turn an otherwise merge-ready PR into a request-changes. The maintainer ends up
manually re-triaging every yellow finding to decide which ones are real blockers, which
defeats the purpose of the buckets.

The feature raises the bar for yellow: a finding stays **Important (yellow, blocking)**
only when it clearly blocks merge or risks real harm if left unfixed. Borderline
"should-fix-but-not-urgent" items are **demoted to Recommendation (green, non-blocking)**;
vague, speculative, or cosmetic items are **dropped**. The result: when the maintainer
sees yellow, it means something, and green carries the useful-but-optional advice.

**Why this priority**: This is the user's second explicit request and directly reduces
false request-changes verdicts, which are the most disruptive review outcome (they block
a mergeable PR). Independently valuable even without Story 1.

**Independent Test**: Assemble a set of findings spanning clear blockers, borderline
should-fix items, and cosmetic nits; run the triage; confirm clear blockers stay yellow,
borderline items become green, and cosmetic/speculative items are dropped — and that the
PR verdict flips to merge-ready when no clear blocker remains.

**Acceptance Scenarios**:

1. **Given** a finding that clearly blocks merge or risks real harm (e.g. an unhandled
   error on a hot path, a broken contract), **When** triage runs, **Then** it stays
   `warning` (🟡 Important) and continues to block the verdict.
2. **Given** a borderline finding — real but not urgent (e.g. "this helper would be
   clearer split in two", "consider covering this branch with a test") — **When** triage
   runs, **Then** it is demoted to `suggestion` (🟢 Recommendation) and no longer blocks
   the verdict.
3. **Given** a vague, speculative, or purely cosmetic finding (e.g. "consider adding a
   docstring", "style could be tidier"), **When** triage runs, **Then** it is dropped and
   does not appear in the posted review.
4. **Given** a PR whose only findings were borderline yellows, **When** triage completes,
   **Then** all are demoted or dropped, no blocking finding remains, and the verdict
   becomes merge-ready (`lgtm: true`) — consistent with the existing "verdict follows
   severity" invariant.
5. **Given** the triage runs, **When** the review is posted, **Then** the verdict color
   grading (green / yellow / red alert) still matches the highest remaining severity, per
   the existing presentation contract.

---

### User Story 3 - Comprehensive multi-perspective discovery, opt-in (Priority: P2)

A maintainer on a high-stakes PR wants the most complete review Kōan can produce. A big
source of run-to-run delta (Story 1) is *incompleteness*: the first review misses issues
that a later run happens to catch, so "new" findings appear that were never really new —
they were simply overlooked the first time. When the operator opts in, `/review` performs
comprehensive discovery — multiple focused passes from a fixed set of independent
perspectives (correctness, security, architecture, silent-failure, test-coverage), using
sub-agents where available — and then **merges and reconciles** all findings into one deduplicated
set before triage. Because each review lands closer to the complete set of real issues,
a subsequent review of unchanged code has little or nothing to add, reinforcing Story 1's
low-delta goal.

This mode costs more tokens and takes longer, so it is **off by default** and enabled via
a configuration option. It is delivered as an **extra prompt partial** included in the
review prompt only when the config option is on; with it off, the review is byte-for-byte
the current single-pass review.

**Why this priority**: It directly amplifies the delta-reduction goal (Story 1) by
attacking incompleteness at the source, but it is an opt-in enhancement rather than the
default path — Stories 1 and 2 deliver their value without it. It can ship after them.

**Independent Test**: On a PR with several independent issues spread across different files
and dimensions, enable comprehensive discovery and confirm the merged review surfaces at
least as many of the known issues as a single-pass review (typically more), with no
duplicate findings for the same underlying issue, and that an immediate re-review of the
unchanged PR adds nothing new. With the option off, confirm the review is identical to
today's single-pass behavior.

**Acceptance Scenarios**:

1. **Given** comprehensive discovery is disabled (the default), **When** `/review` runs,
   **Then** the prompt, findings, cost, and latency are unchanged from today's single-pass
   review — the extra partial is not included.
2. **Given** comprehensive discovery is enabled, **When** `/review` runs on a PR with
   issues spanning multiple dimensions, **Then** the posted review's finding set is a
   superset (by identity, FR-002) of the single-pass review's finding set for that PR — it
   surfaces at least as many real issues, typically more.
3. **Given** two discovery perspectives surface the same underlying issue, **When**
   findings are merged, **Then** they are reconciled into a single finding (no duplicate),
   retaining the highest-justified severity and the clearest explanation.
4. **Given** comprehensive discovery found the near-complete set on the first review,
   **When** `/review` is re-run on unchanged code, **Then** the delta (findings not present
   in the prior review) is empty or minimal.
5. **Given** one discovery perspective fails or times out, **When** merging occurs,
   **Then** the review degrades to the findings from the perspectives that succeeded
   (fail-open) and still posts.
6. **Given** comprehensive discovery is enabled, **When** the merged findings proceed
   downstream, **Then** Story 2's yellow-tier bar and Story 1's consistency/reconciliation
   apply to the merged set exactly as they would to a single-pass set — this story changes
   *how many* findings are discovered, not the severity bar or the verdict rules.

---

### User Story 4 - Visibility into what was demoted or dropped (Priority: P3)

When the review demotes or drops findings, the maintainer wants to trust that nothing
important was silently discarded. A maintainer reviewing a high-stakes PR should be able
to see, at least in a compact form, what the review chose not to surface as blocking and
why — so the filtering is auditable rather than a black box.

**Why this priority**: Improves trust in the aggressive filtering introduced by Stories 1
and 2, but the feature delivers its core value without it. Can ship later without
reworking the earlier stories.

**Independent Test**: Run a review where at least one finding is demoted and one is
dropped; confirm the maintainer can see a compact accounting of demotions/drops (count
and, for demotions, the item) without cluttering the primary review.

**Acceptance Scenarios**:

1. **Given** a review demoted 2 findings and dropped 3, **When** the review is posted,
   **Then** the maintainer can see that filtering occurred (e.g. a compact "N demoted, M
   dropped" note) rather than the filtering being invisible.
2. **Given** the visibility note is present, **When** the maintainer reads the primary
   review, **Then** the note does not compete with or bury the actual blocking findings
   (parsimony — it is secondary, collapsed, or footnoted).

---

### User Story 5 - Exhaustive discovery in a single pass, default prompt (Priority: P2)

Today the default review can stop early — it surfaces the first few problems it notices
and moves on, so real issues go unreported until a later run happens upon them. Since the
FR-003 freeze deliberately withholds non-critical issues that a *later* round newly finds
on unchanged code, the default review's round-1 completeness now matters more than ever:
whatever the first pass misses (below `critical`) stays hidden. This story sharpens the
default `review.md` prompt so a single review pass keeps looking after it finds several
issues and aims to surface **all** issues it can in that one parse — raising round-1 recall
on the default path without requiring the opt-in comprehensive-discovery mode (Story 3).

**Why this priority**: It underpins Stories 1 and the FR-003 freeze — the freeze is only
fair if the first review is thorough. It applies to every review (no opt-in), so it lifts
the default path, but it is a prompt-quality refinement of already-P1 behavior rather than
a new journey. Story 3 remains the heavier opt-in escalation for high-stakes PRs.

**Independent Test**: On a PR seeded with several independent real issues, run one default
review and confirm it surfaces all of them (within coverage), not just the first two or
three — and that the added findings still pass triage so blocking noise does not inflate.

**Acceptance Scenarios**:

1. **Given** a PR containing 6 independent, genuine issues, **When** a single default
   review runs, **Then** it surfaces all 6 (subject to diff/token budget and triage) — it
   does not stop after the first few.
2. **Given** exhaustive discovery surfaces many candidates, **When** triage runs, **Then**
   low-signal/noise items are still filtered and the blocking set (🔴 + 🟡) contains only
   genuine blockers — recall rises without inflating blocking noise (precision does not
   regress, SC-006).
3. **Given** the diff exceeds the budget (partial coverage), **When** the review runs,
   **Then** discovery is exhaustive within the covered files and the omitted files are
   reported via the existing `⚠️ Partial review` contract — "find all" is bounded by
   coverage, never silently truncated.

---

### User Story 6 - Pre-existing issues demoted and labeled (Priority: P2)

An author opens a PR that touches part of a file; the reviewer, reading the surrounding
code, notices a non-critical smell that **predates the PR** — it was already there before
this changeset. Blocking (or even yellow-flagging) the PR for something the author did not
introduce is unfair and noisy. This story makes the reviewer downgrade any **non-critical**
issue that **existed before the changeset** to a Recommendation (🟢, non-blocking) and mark
it with a **`[Pre-Existing Issue]`** title prefix, so the author immediately sees "this
isn't from your change, and it isn't blocking." A `critical` pre-existing issue keeps its
severity (real harm must still be surfaced) but also carries the `[Pre-Existing Issue]`
prefix for the same clarity.

**Why this priority**: It directly serves the "don't block me for things I didn't touch"
frustration and refines Story 2's triage with a concrete, visible rule. It is a
presentation/severity refinement layered on the already-P1 yellow-tier work.

**Independent Test**: Feed the reviewer a diff plus surrounding pre-existing code carrying
one non-critical and one critical pre-existing issue; confirm the non-critical one is a
green `[Pre-Existing Issue]` recommendation (non-blocking) and the critical one keeps
`critical` severity with the same prefix, while an issue the PR actually introduced is
triaged normally without the label.

**Acceptance Scenarios**:

1. **Given** a **non-critical** issue in code the PR did not introduce or modify
   (pre-existing), **When** the review runs, **Then** it is rendered as a `suggestion`
   (🟢 Recommendation) whose title is prefixed `[Pre-Existing Issue]`, and it does **not**
   block the verdict.
2. **Given** a **`critical`** issue in pre-existing code, **When** the review runs,
   **Then** it retains `critical` severity, carries the `[Pre-Existing Issue]` prefix, and
   is surfaced (may block).
3. **Given** a non-critical issue **introduced by the PR** (in the changeset), **When** the
   review runs, **Then** it is triaged normally (Story 2) and is **not** labeled
   pre-existing — the label is reserved for issues that predate the PR.
4. **Given** a re-review where a non-critical pre-existing issue was **not** caught in
   round 1, **When** the re-review runs on code unchanged since the prior review, **Then**
   it stays **suppressed** by the FR-003 freeze and does **not** appear even as a
   `[Pre-Existing Issue]` recommendation (coexist rule — freeze wins on re-review novelty).

---

### User Story 7 - Honor human dispositions from PR comments (Priority: P2)

On a second-or-later review, humans have often already weighed in on the PR — replying to a
finding with "this is fine, ignore it," "that's not actually a problem," or "we'll fix that
in a follow-up." Today the reviewer re-derives its findings and can re-raise the very issue a
human just dismissed, which is a especially maddening form of the "keeps finding the same
things" churn: the human explicitly closed the loop and the bot reopens it. This story makes
the reviewer **read those comments and let them re-categorize the corresponding findings** —
dismissed findings stop being re-raised as blockers, deferred ones drop to non-blocking, and
the reviewer states plainly that it registered the human's input ("noted — treating this as
not an issue per @user"). This embodies Kōan's "the agent proposes, the human decides"
principle and is a direct consistency win on the re-review path.

**Why this priority**: Re-raising a finding a human already dismissed is one of the sharpest
forms of the frustration behind this whole feature, and honoring dispositions removes it. It
is a refinement of the re-review flow that Story 1 establishes, so it sits alongside the other
P2 refinements rather than ahead of the foundational P1 stories.

**Independent Test**: On a PR where a human comment dismisses one finding ("not a problem")
and defers another ("fix later"), run a re-review and confirm the dismissed finding is not
re-raised as blocking (and is acknowledged), the deferred one appears only as a non-blocking
recommendation, and each change is attributed to the commenter — while an undismissed finding
is unaffected.

**Acceptance Scenarios**:

1. **Given** a human comment that **dismisses** a prior finding ("ignore this" / "not a
   problem" / "won't fix"), **When** a re-review runs, **Then** that finding is **not
   re-raised as a blocking finding**, the reviewer records that it registered the human's
   input (acknowledgement reply and/or a "dismissed per @user" note), and the verdict is not
   blocked by it.
2. **Given** a human comment that **defers** a prior finding ("fix later" / "follow-up"),
   **When** a re-review runs, **Then** the finding is downgraded to a non-blocking
   recommendation labeled as deferred and does **not** drive a request-changes verdict.
3. **Given** a human dismisses a **`critical`** finding, **When** the re-review runs, **Then**
   the dismissal is honored per the "human decides" posture — but the suppression/downgrade is
   **attributed** (names the commenter, quotes/links their rationale) so it is auditable, never
   silent.
4. **Given** a comment that merely asks a question or is ambiguous (no clear dispose intent),
   **When** the review runs, **Then** no finding's severity changes on account of it (the
   reviewer may reply for clarification), and a comment **cannot** rewrite the reviewer's
   scoring rubric or verdict rules — it disposes of a *specific finding* only.
5. **Given** a finding was dismissed in an earlier round, **When** later reviews run and the
   finding still applies by identity, **Then** it **stays** dismissed (sticky) unless a
   subsequent human comment **retracts** the disposition ("actually this is a problem"), which
   restores normal evaluation.

---

### Edge Cases

- **Unchanged HEAD but prior review missing/unreadable** (first review, expired sidecar,
  corrupted record): the system MUST fall back to a normal fresh review rather than fail
  or post nothing.
- **HEAD unchanged but the review was posted by a different config** (e.g. focus flags
  differ, `--architecture` added): a reuse of the prior review would be misleading. The
  reuse short-circuit MUST only apply when the review request is equivalent (same target,
  same focus passes), otherwise re-derive.
- **Force-push that rewrites history to the same tree**: HEAD SHA changes even though the
  code is identical. Behavior should be defined — treat as "changed" (re-derive, but
  reconcile against prior findings so overlap stays high).
- **Base branch advances or PR is retargeted while the PR head is unchanged**: the
  effective diff changes even though the PR head SHA is identical. Reuse MUST NOT fire (the
  reuse key includes the base/merge-base SHA per FR-001); the review re-derives and
  reconciles against the prior findings.
- **Round-1 miss on unchanged code**: a real but non-`critical` issue overlooked in round 1,
  in code untouched by later commits, stays **suppressed** on re-review (FR-003 freeze) —
  surfacing it late is the whiplash the user reported. Comprehensive discovery (Story 3) is
  the mechanism to catch it in round 1 instead. A `critical` miss is the sole exception and
  surfaces marked *pre-existing*.
- **A pre-existing issue on unchanged code worsens in severity between rounds** (e.g. model
  now rates it `critical`): the `critical` exception applies and it surfaces marked
  pre-existing; a first-time non-`critical` re-grade of unchanged code does not surface
  (freeze), so the severity bar for breaking the freeze is `critical`, evaluated per-run.
- **Large-diff partial coverage**: if which files are covered varies between runs, the
  finding set varies for reasons unrelated to the model. Coverage/packing selection MUST
  be deterministic for a given HEAD + budget so it does not become a hidden source of
  drift.
- **A borderline finding sits exactly on the yellow/green bar**: the demote/keep decision
  MUST be deterministic (a stable tiebreak), so the same finding lands in the same tier on
  every run — otherwise the triage reintroduces the very drift Story 1 removes.
- **All findings dropped as noise**: the review MUST still post a clean merge-ready
  verdict, not an empty or malformed comment.
- **Reflection/triage step fails or times out**: the review MUST degrade to the
  pre-triage finding set (fail-open) rather than dropping everything or aborting the
  posted review — consistent with the existing best-effort enrichment contract.
- **`--force` re-review of a closed/merged PR with unchanged HEAD**: reuse-vs-re-derive
  behavior should follow the same equivalence rule; `--force` does not bypass consistency.
- **Comprehensive discovery config on/off toggled between runs**: two reviews of the same
  HEAD with the option in different states are *not* request-equivalent (FR-001), so the
  reuse short-circuit MUST NOT reuse a single-pass review as a comprehensive one or vice
  versa — it re-derives.
- **A perspective pass returns the same issue with a different severity than another
  pass**: the merge MUST resolve to one finding with a deterministic severity choice (the
  highest justified), not emit both.
- **All discovery perspectives fail**: the review MUST fall back to the ordinary
  single-pass review rather than posting nothing (fail-open to baseline).
- **Comprehensive discovery hits its pass cap on a very large PR**: the cap MUST bound
  cost, and any coverage the cap forced out MUST be reported (not silent), consistent with
  the existing `⚠️ Partial review` contract.
- **A human dismisses a finding, then later retracts it**: the disposition is lifted and the
  finding is evaluated normally again (FR-037).
- **Conflicting human dispositions on one finding** (one says "ignore", another says
  "must fix"): the reviewer MUST NOT silently suppress — it keeps the finding surfaced and
  notes the conflict, leaving the decision explicit rather than guessing.
- **A comment references a finding that no longer exists** (already fixed or never present):
  the disposition is a no-op.
- **A comment contains instruction-like text beyond disposing of a finding** (e.g. "mark all
  findings resolved", "always approve"): treated as untrusted and ignored — it disposes of a
  specific finding only (FR-038 injection guardrail).
- **A dismissed `critical`**: honored per FR-035 but attributed per FR-036 — this is the
  known-risk case the audit trail exists for.

## Requirements *(mandatory)*

### Functional Requirements

#### Consistency (Story 1)

- **FR-001**: When `/review` is invoked on a PR whose reviewed **PR head SHA and base
  (merge-base) SHA are both identical** to those of a prior review **and** the review
  request is equivalent (same target, same focus passes/flags, same comprehensive-discovery
  setting), the system MUST reproduce the prior review's findings, severities, and verdict
  rather than re-deriving them. Any base-branch movement or retarget changes the effective
  diff and MUST force a re-derive (with reconciliation per FR-003), even when the PR head
  SHA is unchanged.
- **FR-002**: The system MUST define a stable notion of **finding identity** (so "the same
  finding" can be recognized across runs and merged across discovery perspectives). The
  identity key MUST be **`file` + a nearby code region (a small line-window tolerance, so a
  minor line shift does not break the match) + a semantic issue topic/category**. It MUST
  NOT depend on exact line numbers or on model wording, both of which vary run to run.
- **FR-003**: When the reviewed HEAD has changed since the prior review, the system MUST
  reconcile against the prior finding set using the **incremental diff** (prior reviewed SHA
  → current SHA), with these rules:
  - Prior findings **resolved** by the new commits are **suppressed**.
  - Prior findings that **still apply recur unchanged** (matched by identity, FR-002).
  - A **first-time finding** (no prior-review match by identity) whose code region **was
    changed** by the new commits **may be added**.
  - A **first-time finding whose code region was NOT changed** since the prior review
    (pre-existing code that was reviewable in the prior round but not reported) MUST be
    **suppressed** — this is the "review whiplash" case — **except** when its severity is
    `critical` (real-harm/security), which MUST still surface, carrying the
    `[Pre-Existing Issue]` prefix (FR-028).
  In effect, re-reviews are **additive on changed code and frozen on unchanged code**
  (modulo the `critical` safety valve). This applies to the merged set when comprehensive
  discovery (Story 3) is enabled, too; round-1 completeness (Story 3) is the intended way to
  minimize what the freeze withholds, since anything genuinely important missed in round 1
  and below `critical` stays frozen on later rounds by design.
- **FR-004**: The system MUST reduce run-to-run variance in the finding-derivation path so
  that, even when re-derivation occurs, the blocking finding set (🔴 + 🟡) is stable for
  the same input.
- **FR-005**: The diff coverage/packing decision (which files are reviewed when a diff
  exceeds the budget) MUST be deterministic for a given HEAD and budget, so partial
  coverage is not a source of finding drift.
- **FR-006**: A reproduction of a prior review MUST be distinguishable to the reader from a
  fresh review (the maintainer can tell the review was reproduced rather than freshly
  re-rolled), and MUST NOT silently no-op.
- **FR-007**: If a prior review cannot be reused (missing, unreadable, or non-equivalent
  request), the system MUST fall back to a fresh review without error.

#### Yellow-tier triage (Story 2)

- **FR-008**: The system MUST enforce an explicit, testable bar for the `warning` (🟡
  Important) severity: a finding qualifies as Important **only** if it clearly blocks merge
  or risks real harm if left unfixed.
- **FR-009**: A finding that does not meet the Important bar but is still a legitimate,
  actionable improvement MUST be **demoted** to `suggestion` (🟢 Recommendation), keeping
  it visible and non-blocking.
- **FR-010**: A finding that is vague, speculative, unverifiable from the diff/codebase, or
  purely cosmetic MUST be **dropped** and not posted.
- **FR-011**: The demote-vs-drop-vs-keep decision MUST be deterministic for a given finding
  and input, so it does not reintroduce run-to-run drift (ties resolve the same way every
  run).
- **FR-012**: The verdict (`lgtm`) MUST continue to be derived from the **post-triage**
  severities: `lgtm: false` only if a `critical` or `warning` finding survives triage;
  otherwise `lgtm: true`. This preserves and reinforces the existing "verdict follows
  severity" invariant.
- **FR-013**: The triage MUST be fail-open: if the triage step errors or times out, the
  review degrades to the pre-triage finding set and still posts, rather than dropping all
  findings or aborting.
- **FR-014**: The yellow-tier bar MUST be configurable (operators can tune how strict
  "Important" is) with a safe default, and MUST remain backward-compatible (absent config →
  current behavior is not silently made more aggressive without a default that the operator
  can see and adjust).

#### Comprehensive discovery (Story 3)

- **FR-015**: The system MUST support a **comprehensive-discovery mode** in which the review
  performs multi-perspective discovery across a **fixed, defined set of perspectives
  enumerated in the prompt partial** — correctness, security, architecture, silent-failure,
  and test-coverage (using sub-agents where the provider supports them) — and merges the
  results into one finding set. Operators toggle the whole mode on/off; individual
  perspectives are not separately configurable.
- **FR-016**: Comprehensive discovery MUST be delivered as an **includable prompt partial**
  gated by a **configuration option that is disabled by default**. When disabled, the review
  prompt and behavior MUST be byte-for-byte the current single-pass review — no extra passes,
  no added cost, no added latency.
- **FR-017**: When enabled, the merged findings MUST be **deduplicated by the finding-identity
  notion (FR-002)** so the same underlying issue reported from multiple perspectives appears
  exactly once, retaining the highest-justified severity and the clearest explanation.
- **FR-018**: The merged, deduplicated finding set MUST feed the **same downstream triage
  (Story 2) and consistency/reconciliation (Story 1)** unchanged. Comprehensive discovery
  affects only *how many* real findings are discovered — never the severity bar, the dedup
  identity, or the verdict rules.
- **FR-019**: Comprehensive discovery MUST be **fail-open and bounded**: a failed or stalled
  perspective degrades to the findings of the perspectives that succeeded, all perspectives
  failing degrades to the ordinary single-pass review, and the total number of passes is
  capped so cost cannot grow unbounded.
- **FR-020**: The configuration option MUST be a **single on/off toggle** for the whole mode
  (not a per-perspective list), follow existing review-config conventions (per-instance with
  per-project override), and be documented so operators understand its cost and latency
  tradeoff before enabling it.
- **FR-021**: When the pass cap forces reduced coverage on a large PR, that partial coverage
  MUST NOT be silent — it MUST be reported consistent with the existing partial-coverage
  contract.

#### Cross-cutting (evals & contract)

- **FR-022**: All behavior changes MUST be reflected in the review skill's eval harness — the
  golden dataset and baseline MUST measure repeat-review stability, the sharpened yellow-tier
  calibration, and (when enabled) the recall gain and dedup correctness of comprehensive
  discovery, per the review skill's evaluation contract.
- **FR-023**: The durable review contract (`specs/skills/review.md`) MUST be updated
  contract-first to document the consistency guarantee, the yellow-tier bar, and the opt-in
  comprehensive-discovery mode, since these change the skill's observable contract (an
  architectural change).

#### Visibility (Story 4)

- **FR-024**: When the review demotes or drops findings, the posted review SHOULD surface a
  compact, secondary accounting that filtering occurred (at minimum counts; ideally the
  demoted items), without burying the primary blocking findings.

#### Exhaustive single-pass discovery (Story 5)

- **FR-025**: The default (single-pass) `review.md` prompt MUST instruct the reviewer to
  **keep searching for issues after finding several** and to aim to surface **all** issues
  it can identify in one review pass, rather than stopping early. The prompt MUST NOT impose
  or imply a cap on the number of findings (coverage/triage bound them, not an arbitrary
  limit).
- **FR-026**: Exhaustive discovery MUST NOT weaken precision or severity discipline: the
  additional findings it surfaces still pass the same reflection/noise filtering and
  yellow-tier triage (Story 2), so higher recall does not inflate the blocking set. This is
  a default-path, always-on prompt behavior (not gated by the Story 3 opt-in mode).

#### Pre-existing issue handling (Story 6)

- **FR-027**: A finding whose underlying issue **predates the changeset** (present in the
  code before the PR's changes, i.e. not introduced or modified by the PR) and whose
  severity is **not `critical`** MUST be downgraded to `suggestion` (🟢 Recommendation,
  non-blocking) and MUST have its title prefixed with **`[Pre-Existing Issue]`**.
- **FR-028**: A **`critical`** pre-existing issue MUST **retain** its `critical` severity
  (real harm must still surface and may block) but MUST also carry the `[Pre-Existing Issue]`
  title prefix so the author knows it predates their change. (Unifies the FR-003 critical
  re-review exception's "marked as pre-existing" with this concrete label.)
- **FR-029**: The `[Pre-Existing Issue]` prefix MUST be applied consistently so the same
  pre-existing finding is labeled identically across runs (deterministic per finding
  identity, FR-002). Determining "pre-existing" is the reviewer's semantic assessment that
  the issue exists prior to the PR's changes; the label and demotion live in the finding's
  title/severity and require **no review output-schema change**.
- **FR-030**: The `[Pre-Existing Issue]` demotion/labeling (FR-027–FR-029) governs how
  pre-existing findings are **presented when they surface**; it MUST NOT override the FR-003
  re-review freeze. On a re-review, a first-time **non-critical** pre-existing finding on
  code unchanged since the prior review remains **suppressed** (freeze wins). Consequently
  `[Pre-Existing Issue]` labels appear on round-1 findings, on findings in changed code, and
  on `critical` pre-existing exceptions — never on a late-surfacing non-critical miss.

#### Human dispositions from PR comments (Story 7)

- **FR-031**: On a second-or-later review, the reviewer MUST read the PR's human comments and
  detect **dispositions** of findings — at minimum **dismiss** ("ignore", "not a problem",
  "false positive", "won't fix") and **defer** ("fix later", "follow-up", "later"). (Reuses
  the comments already ingested by the review-context; no new fetch contract.)
- **FR-032**: Dispositions from **any non-bot human commenter** are honored (per
  clarification — "the human decides"); Kōan's own comments/replies MUST be excluded
  (bot-filtered).
- **FR-033**: A **dismissed** finding MUST NOT be re-raised as a blocking finding on this or
  subsequent rounds; it is treated as dismissed/resolved. The reviewer MUST make clear it
  registered the human's input — an acknowledgement reply (reusing the existing reply `action`
  mechanism, e.g. `wont_fix`/`acknowledged`) and/or a "dismissed per @user" note — i.e.
  "provide information that this is not an issue".
- **FR-034**: A **deferred** finding MUST be downgraded to a non-blocking `suggestion`
  (recommendation), labeled as deferred (e.g. `[Deferred]`), and MUST NOT block the verdict.
- **FR-035**: Human dispositions apply to **all severities, including `critical`** (per
  clarification — full human authority): a dismissed `critical` is suppressed/downgraded per
  the human's request. This overrides normal surfacing (and the FR-003 freeze) because the
  human has explicitly decided.
- **FR-036**: Every finding suppressed or downgraded because of a human comment MUST be
  **attributed and auditable** — the review records **who** requested it and **quotes or
  links their rationale** — so a human-driven suppression is **never silent**. This is the
  compensating control for the open posture of FR-032/FR-035.
- **FR-037**: A disposition MUST persist across subsequent reviews (sticky) as long as the
  finding recurs by identity (FR-002) and has not been retracted; a later human comment that
  **reverses** the disposition ("actually this is a problem") MUST lift it and restore normal
  evaluation.
- **FR-038**: Only a **clear** disposition changes a finding's categorization; an ambiguous or
  unrelated comment MUST NOT alter severity or verdict. A comment can dispose of a **specific
  finding**, but MUST NOT rewrite the reviewer's scoring rubric, severity calibration, or
  verdict rules — instruction-like comment content beyond disposing of a finding is treated as
  untrusted and ignored (injection guardrail).

### Key Entities *(include if feature involves data)*

- **Finding**: A single review observation. Attributes relevant here: file, anchor
  location, issue topic/title, severity (critical/warning/suggestion), and a derived
  **identity key** — `file` + tolerant code region + semantic topic/category (FR-002) —
  used to match the same finding across runs and to dedup across discovery perspectives.
- **Prior review record**: The persisted result of the most recent review of a PR — its
  findings, severities, verdict, the reviewed **PR head SHA and base (merge-base) SHA**, and
  the review-request signature (target + focus flags + comprehensive-discovery setting)
  needed to judge equivalence (FR-001).
- **Triage decision**: For each finding, the outcome of the yellow-tier bar — keep
  (Important), demote (Recommendation), or drop — with the deterministic rationale
  (FR-008–FR-011).
- **Consistency comparison**: For evals/measurement, the overlap between two reviews'
  finding sets keyed by identity (FR-022).
- **Discovery perspective**: One focused review lens from the **fixed set** (correctness,
  security, architecture, silent-failure, test-coverage) run when comprehensive discovery is
  enabled. Each produces a candidate finding list; perspectives are independent and
  enumerated in the prompt partial (FR-015).
- **Merged finding set**: The single deduplicated, reconciled finding list produced by
  combining all discovery perspectives (FR-017), which then feeds triage and consistency
  exactly as a single-pass list would (FR-018).
- **Comprehensive-discovery config option**: The per-instance/per-project switch (default
  off) that includes the discovery prompt partial; also part of the review-request
  signature so a single-pass review is never reused as a comprehensive one (FR-016,
  FR-001 equivalence).
- **Pre-existing marker**: A per-finding property — the reviewer's assessment that the
  issue predates the changeset — surfaced as the `[Pre-Existing Issue]` title prefix and, for
  non-critical findings, as a forced `suggestion` severity (FR-027–FR-030). Carried in the
  finding's title/severity; no output-schema change.
- **Human disposition**: A non-bot human PR comment that dispositions a finding — its **kind**
  (dismiss / defer / retract), the **commenter identity**, the **quoted rationale**, and the
  **target finding** it addresses (by inline location or reply threading). Drives suppression/
  downgrade with mandatory attribution (FR-031–FR-038); sticky by finding identity until
  retracted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Re-running `/review` on a PR with no new commits reproduces the prior
  review's **blocking finding set (🔴 + 🟡) with 100% overlap** and an identical verdict.
- **SC-002**: Across a representative sample of PRs, re-running `/review` on unchanged code
  yields **≥ 95% overlap of all surfaced findings** (blocking + recommendations) by
  identity key, up from today's observed drift.
- **SC-003**: After a commit that fixes one finding and changes nothing else, the next
  review **suppresses the fixed finding, retains 100% of the still-applicable findings
  unchanged**, and adds new findings only where the new commit introduced them.
- **SC-004**: On a labeled dataset of findings, **≥ 90% of items a maintainer classifies as
  non-blocking are demoted to green or dropped** (i.e. do not remain yellow), while **100%
  of true blockers remain yellow/red** (no under-triage of genuine blockers).
- **SC-005**: The rate of PRs that receive a request-changes verdict driven **solely** by
  borderline yellow findings drops by **≥ 50%** compared to the pre-feature baseline.
- **SC-006**: Review precision (no findings flagged on known-clean/forbidden files) does not
  regress from the current eval baseline, and recall of seeded genuine bugs does not regress.
- **SC-007**: The consistency and triage behaviors are covered by the eval harness such that
  a regression in either (drift returns, or the yellow bar loosens) fails CI/live evals.
- **SC-008**: With comprehensive discovery **disabled** (the default), a review's finding set,
  token cost, and latency are **identical** to the pre-feature single-pass review — the
  default path shows zero regression.
- **SC-009**: With comprehensive discovery **enabled**, on a labeled multi-issue dataset,
  recall of seeded genuine issues improves by **≥ 20%** versus single-pass, with **0 duplicate
  findings** for the same underlying issue (dedup correctness = 100% by identity key).
- **SC-010**: With comprehensive discovery **enabled**, an immediate re-review of unchanged
  code adds **≤ 1 new finding on average** (near-zero delta), demonstrating the
  completeness→low-delta link that motivated the mode.
- **SC-011**: In the fix-and-repush scenario (prior findings fixed, only those areas
  touched), a re-review introduces **zero first-time `warning`/`suggestion` findings on code
  unchanged since the prior review** (whiplash rate = 0). Only `critical` pre-existing
  findings may appear on unchanged code, and each is labeled `[Pre-Existing Issue]`.
- **SC-012**: On a labeled multi-issue dataset, a single **default** review surfaces
  **≥ 90% of the seeded genuine issues within covered files** (recall), demonstrating the
  prompt does not stop early — measured without the Story 3 opt-in mode.
- **SC-013**: **100%** of non-critical findings about pre-existing (before-the-changeset)
  code are rendered as 🟢 recommendations prefixed `[Pre-Existing Issue]` and **none** cause
  a request-changes verdict; `critical` pre-existing findings retain `critical` severity and
  also carry the prefix.
- **SC-014**: Once a human comment **dismisses** a finding, subsequent reviews **re-raise it
  as blocking 0% of the time** while the disposition stands — the dismissed-then-re-raised
  churn is eliminated.
- **SC-015**: A **deferred** finding never drives a request-changes verdict on later rounds
  (0% of deferred findings block the merge decision).
- **SC-016**: **100%** of comment-driven suppressions/downgrades are attributed — each names
  the requesting commenter and quotes or links the rationale; there is **no silent
  suppression** (auditable in every case, including dismissed `critical`s).

## Assumptions

- **Existing severity vocabulary is retained.** The three tiers (critical/warning/suggestion
  → 🔴/🟡/🟢) and their mapping to the `lgtm` verdict are unchanged; this feature tightens
  *what qualifies* for yellow, not the tier set itself.
- **Consistency strategy is "stabilize + reuse when unchanged"** (per clarification): reuse
  the prior review verbatim when HEAD is byte-identical and the request is equivalent, and
  otherwise re-derive with reduced variance + reconciliation against the prior finding set.
- **Yellow-tier default policy is the impact bar** (per clarification): keep as Important
  only clear merge-blockers / real-harm items; demote borderline should-fix items to green;
  drop vague/speculative/cosmetic items.
- **The prior review is already persisted** in a form this feature can read (the current
  branch reads an existing-review sidecar for reconciliation); consistency reuse builds on
  that persistence rather than introducing a new store.
- **Reproducibility is best-effort, not a hard guarantee against all model nondeterminism.**
  The reuse path gives exact reproduction for unchanged HEAD; the re-derive path targets
  high overlap, not byte-identical output, and fails open.
- **Re-review UX (fresh comment + collapse-prior, stale-HEAD alert, partial-coverage
  warning) is preserved** — this feature changes *which findings* appear and *how stable*
  they are, not the posting/collapse mechanics documented in `specs/skills/review.md`.
- **Configuration is per-instance with per-project override**, consistent with existing
  review config keys (`review_reflect`, `review_reconcile`, etc.).
- **Comprehensive discovery is delivered as an includable prompt partial, not a Python
  orchestration layer** (per clarification). The discovery/reconciliation guidance lives in
  a `{@include …}` fragment added to the review prompt only when the config option is on;
  whether the reviewing agent fulfils it by delegating to sub-agents or by structured
  multi-perspective reasoning is a prompt-content detail, not a spec requirement.
- **Comprehensive discovery is off by default** (per clarification): normal `/review` is
  unchanged, and the mode is enabled deliberately by an operator who accepts the higher cost
  and latency for a more complete, lower-delta review.
- **Exhaustive single-pass discovery is a default-on prompt change** (Story 5): it sharpens
  the always-used `review.md` prompt (no config gate, no opt-in), distinct from the opt-in
  multi-agent mode of Story 3. "Single parse" = one review pass, made thorough.
- **Pre-existing labeling is prompt-level and schema-free** (Story 6, per clarification):
  the `[Pre-Existing Issue]` prefix lives in the finding title and the demotion reuses the
  existing `suggestion` severity, so no review output-schema change is required. Whether a
  programmatic diff-based assist supplements the reviewer's judgment is a planning decision.
- **Pre-existing vs freeze coexist** (per clarification): the `[Pre-Existing Issue]`
  demote/label rule governs *presentation of findings that surface*; the FR-003 freeze
  governs *whether a first-time non-critical finding on unchanged code surfaces at all on a
  re-review* (it does not). The freeze wins on re-review novelty.
- **Human dispositions follow "the agent proposes, the human decides"** (per clarification):
  any non-bot commenter can dismiss/defer a finding, for all severities including `critical`.
  This is a deliberate, philosophy-aligned posture. **Known tradeoff:** it widens a
  social-engineering / prompt-injection surface (anyone commenting can downgrade or suppress a
  finding, including a critical). It is mitigated, not eliminated, by mandatory attribution/
  audit (FR-036) and the guardrail that a comment disposes of a *specific finding* and cannot
  rewrite the scoring rubric or verdict rules (FR-038). An operator wanting a stricter posture
  (authorized-users-only, or protect-criticals) could layer a config guard — left to planning;
  the default stays open per this choice.
- **Comment handling reuses existing plumbing**: PR comments are already ingested by the
  review-context partial, and acknowledgements reuse the existing reply `action` mechanism —
  so Story 7 needs **no review output-schema change** and no new fetch contract; it adds the
  interpretation + finding re-categorization layer.
- **Dispositions are sticky by finding identity** (FR-002) until the human retracts them.
- **Scope is the `/review` skill's finding set, severity triage, (opt-in) discovery
  completeness, exhaustive default-prompt recall, pre-existing labeling, and honoring human
  dispositions on re-review.** Out of scope: redesigning the color scheme, changing
  `/fix`/`/rebase` behavior, human-review learning (`pr_review_learning.py`) beyond feeding
  calibration, and non-review skills.
