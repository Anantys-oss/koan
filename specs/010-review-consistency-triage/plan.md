# Implementation Plan: Review Consistency, Yellow-Tier Triage & Comprehensive Discovery

**Branch**: `feat/review-accuracy-and-repo-context` | **Date**: 2026-07-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-review-consistency-triage/spec.md`

## Context: what docs/specs said (mandatory consult)

Consulted via the index-first `/brain` path (`wiki/index.md`) and by reading the authoritative
durable contract and adjacent code:

- **`specs/skills/review.md`** (durable contract — authoritative). Already codifies:
  verdict-follows-severity (`lgtm` false iff a `critical`/`warning` survives); the reflection
  pass + post-reflection finalization; re-review comment handling (fresh comment +
  `_collapse_old_review`, `review_history.preserve_previous`); the stale-HEAD alert; core
  review posted **before** best-effort enrichment passes; the diff-size/partial-coverage
  contract. **This feature changes that contract**, so the spec must be updated *contract-first*
  (spec FR-023; Constitution Principle II) — see "Architectural change" below.
- **`docs/operations/skill-evals.md`** — `review` is the first skill in the deterministic eval
  harness (`koan/app/skill_evals.py`); golden dataset at `koan/skills/core/review/evals/`,
  offline scorer in CI (`fast` group), live mode `KOAN_EVAL_LIVE=1`. Any behavior change must be
  reflected in the dataset/baseline (spec FR-022).
- **`docs/architecture/github-and-trackers.md`** — receiving-code-review protocol and review
  issue-context enrichment; PR comment ingestion already exists (the `review-context` partial
  exposes existing/repliable comments).
- **`docs/security/threat-model-agent-disalignment.md`** + **`docs/security/prompt-guard.md`** —
  human PR review is the primary security boundary; comment content is untrusted. Directly bears
  on US7 (honoring human dispositions): the open posture the user chose is philosophy-aligned
  (Constitution Principle I: "the agent proposes, the human decides") but widens the injection
  surface — hence the FR-036 audit + FR-038 rubric guardrail.
- **`docs/design/spec-changes-are-architectural.md`** + **Constitution Principle II** — durable
  spec changes are contract-first, rare, declared, and enforced by `scripts/spec_change_guard.py`.

Nothing in the index describes review *determinism/consistency* or *pre-existing labeling* — the
absence confirms this is new contract surface, not a re-tread.

## Summary

Make `/review` **consistent, correctly triaged, and human-aware** across repeated runs. Seven
capabilities (spec US1–US7), delivered as prompt changes + focused Python helpers around the
existing pipeline, with the review-findings **sidecar** as the persistence seam:

1. **Consistency (US1)** — reuse the prior review verbatim when PR head **and** base/merge-base
   SHA match and the request signature is equivalent; otherwise re-derive and reconcile against
   the prior finding set with a **re-review freeze** (first-time non-`critical` findings on code
   unchanged since the prior review are suppressed; `critical` breaks through, labeled).
2. **Yellow-tier bar (US2)** — an explicit, testable "Important" bar: keep only clear
   blockers/real-harm; demote borderline to recommendation; drop noise. Verdict re-derived from
   post-triage severities.
3. **Comprehensive discovery (US3)** — opt-in prompt partial (config off by default) that runs a
   fixed set of perspectives and merges/dedups into one set.
4. **Visibility (US4)** — compact "N demoted, M dropped" accounting.
5. **Exhaustive single-pass (US5)** — the default `review.md` prompt keeps looking, aiming to
   surface all issues in one parse (always on).
6. **Pre-existing labeling (US6)** — non-`critical` issues predating the changeset →
   `suggestion` + `[Pre-Existing Issue]` prefix; `critical` keeps severity + prefix.
7. **Human dispositions (US7)** — on a re-review, honor PR comments that dismiss/defer a finding
   (any non-bot commenter, all severities), with mandatory attribution and an injection guardrail.

**Approach**: mostly prompt engineering (US2, US3, US5, US6 rubric, US7 interpretation) reusing
the existing `{@include}` partial mechanism, plus a small number of focused Python helper modules
for the deterministic parts that must not depend on model variance (finding identity, reuse
short-circuit, incremental-diff freeze, disposition matching, config, sidecar schema). No review
output-schema change is required (labels live in finding titles; dispositions reuse the reply
`action` mechanism).

## Technical Context

**Language/Version**: Python 3.11+ (repo-wide floor; no 3.12+ syntax).

**Primary Dependencies**: existing `koan/app/review_runner.py` pipeline, `review_schema.py`
(`validate_review`), the `{@include}` prompt loader (`prompts.py`), `config.py`
`_get_config_with_overrides` convention, `github.py`/`gh` for comment fetch, the review-findings
sidecar (`.review-findings/*.json`), and `skill_evals.py`.

**Storage**: per-PR JSON sidecar under `instance/.review-findings/` (already exists via
`_write_review_findings_sidecar`/`_read_prior_findings_sidecar`) — extended, not replaced. No DB.

**Testing**: `pytest` with `KOAN_ROOT` set; unit tests per helper (behavior, not implementation);
offline eval scorer in CI `fast` group; `KOAN_EVAL_LIVE=1` live eval before/after prompt changes.
Never call the Claude subprocess in tests (mock the provider boundary).

**Target Platform**: the Kōan agent loop (background missions) + GitHub PRs.

**Project Type**: single Python package (`koan/`) — the review skill and its runner.

**Performance Goals**: default path (US5, and US3-off) shows **zero** cost/latency regression
(spec SC-008). Reuse short-circuit avoids a provider call entirely for unchanged HEAD.
US3-on cost is bounded by a pass cap; the mode is off by default.

**Constraints**: fail-open everywhere (triage/discovery/disposition failure degrades to the
prior behavior, never drops all findings or aborts a posted review); determinism for the
consistency-critical paths (identity, reuse key, freeze, tiebreaks) must live in Python, not in
model output; no review output-schema change; `review_runner.py` is already **4,188 lines**
(≫ 600-line soft limit) so new logic goes in **sibling modules**, not appended.

**Scale/Scope**: 7 user stories, 38 FRs, 16 SCs; ~6 new focused modules + prompt edits + config
keys + eval dataset extensions.

## Constitution Check

*GATE: must pass before Phase 0; re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| **I. Human Authority** | ✅ PASS (reinforced) | `/review` stays advisory — posts comments, never merges/pushes (spec invariant preserved). US7 *increases* human authority (comments override findings). The open US7 posture is Principle-I-aligned ("human decides") with FR-036 audit + FR-038 guardrail as compensating controls; the injection tradeoff is documented in the spec Assumptions and flagged for PR review. |
| **II. Specs Are the Source of Truth** | ⚠️ ARCHITECTURAL CHANGE (declared) | This feature changes the durable `specs/skills/review.md` contract (consistency guarantee, yellow bar, opt-in discovery, `[Pre-Existing Issue]`, human dispositions). Must be **contract-first**: update `specs/skills/review.md` **before/with** code, and **declare** it in the PR ("Architectural change" box). `scripts/spec_change_guard.py` will otherwise fail CI. This is the one gate that needs explicit handling — see Complexity Tracking. |
| **III. State authority** | ✅ PASS | No mission-store change. New durable state is the per-PR review sidecar (file-first, best-effort, fail-open) — consistent with "all other runtime state file-first". |
| **IV. Fail loud at boundaries** | ✅ PASS | Comment parsing / discovery / triage validate inputs and fail *open to the prior behavior* (a boundary degrade, not a silent swallow); FR-036 makes disposition-driven suppression loud (attributed). |
| **V. Git-enforced controls are load-bearing** | ✅ PASS | Behavior guarded by the eval harness (CI `fast` + live) and the spec-change guard, not prose. |

**Result**: PASS with one **declared architectural change** (Principle II) — expected and in-scope
(spec FR-023). No unjustified violations.

## Project Structure

### Documentation (this feature)

```text
specs/010-review-consistency-triage/
├── plan.md              # this file
├── spec.md              # feature spec (7 stories, 38 FR, 16 SC)
├── research.md          # Phase 0 — decisions per unknown
├── data-model.md        # Phase 1 — entities (sidecar, identity, disposition, triage)
├── contracts/           # Phase 1 — config keys, prompt-partial includes, sidecar schema
│   ├── config-keys.md
│   ├── prompt-partials.md
│   └── review-sidecar.schema.json
├── quickstart.md        # Phase 1 — how to validate (evals + manual re-review)
└── checklists/requirements.md
```

### Source code (repository root) — new/changed

```text
koan/app/
├── review_runner.py                 # (existing, 4188 lines) — WIRE new helpers in; do not grow
├── review_identity.py               # NEW — finding-identity key (file + tolerant region + topic)
├── review_reuse.py                  # NEW — reuse short-circuit + request-signature equivalence
├── review_reconcile.py              # NEW — incremental-diff freeze + recurrence/suppression
│                                    #        (extracts today's _reconcile_* logic out of runner)
├── review_dispositions.py           # NEW — parse human PR-comment dispositions → finding actions
├── review_triage.py                 # NEW — yellow-tier bar + pre-existing labeling (deterministic
│                                    #        post-pass over model severities; prompt does the rest)
├── config.py                        # +get_review_consistency_config / _triage / _discovery /
│                                    #  _dispositions getters (existing convention)
└── review_schema.py                 # (unchanged shape) — validators may gain helpers, no new fields

koan/skills/core/review/prompts/
├── review.md                        # EDIT — exhaustive discovery (US5), pre-existing rubric (US6),
│                                    #        disposition-awareness (US7), sharper yellow bar (US2)
├── review-with-plan.md              # EDIT — same rubric deltas kept in sync
└── review-architecture.md           # EDIT — same rubric deltas kept in sync

koan/system-prompts/_partials/
├── review-comprehensive-discovery.md  # NEW — opt-in partial (US3), included only when enabled
├── review-severity-rubric.md          # NEW — shared yellow bar + [Pre-Existing Issue] rules,
│                                       #  {@include}-d by the three review prompts (DRY)
└── review-dispositions.md             # NEW — how to read/honor human dispositions (US7)

koan/skills/core/review/evals/
├── cases/*.json                     # ADD: repeat-stability, pre-existing, disposition, recall
└── baseline.json                    # UPDATE

specs/skills/review.md               # EDIT (contract-first, declared) — new invariants
instance.example/config.yaml         # ADD documented default-off/default config blocks
docs/users/{user-manual.md,skills.md}, docs/architecture/… # capture (per CLAUDE.md)
```

**Structure Decision**: single-package. Because `review_runner.py` is already ~4,188 lines, every
new unit of deterministic logic lands in a **focused sibling module** (`review_identity`,
`review_reuse`, `review_reconcile`, `review_dispositions`, `review_triage`) that the runner calls;
the runner only gains thin wiring. Prompt behavior is factored into `_partials/` and `{@include}`-d
so the three review prompt variants stay in sync (DRY). Config follows the existing
`get_review_*_config()` + `_get_config_with_overrides` convention.

## Phasing (story → build order)

Sequenced so each phase is independently shippable and testable (spec priorities: US1/US2 = P1
foundation; US5/US6/US7/US3 = refinements; US4 = P3).

- **Phase A — deterministic core (US1 + US2 substrate)**: `review_identity`, `review_reuse`
  (+ extend sidecar with base SHA + request signature), `review_reconcile` (incremental-diff
  freeze), and the deterministic `review_triage` post-pass. Wire into the runner. Delivers the
  anti-whiplash guarantee (SC-001/002/003/011).
- **Phase B — prompt rubric (US2 bar, US5 exhaustive, US6 pre-existing)**: edit `review.md` +
  shared `_partials/review-severity-rubric.md`; keep the three prompt variants in sync. Pairs
  with the `review_triage` deterministic backstop from Phase A.
- **Phase C — human dispositions (US7)**: `review_dispositions` + `review-dispositions.md`;
  extend sidecar to persist dispositions (stickiness); attribution rendering.
- **Phase D — comprehensive discovery (US3, opt-in)**: `review-comprehensive-discovery.md`
  partial + config gate + merge/dedup reuse of `review_identity`.
- **Phase E — visibility (US4)** + **evals/docs/contract**: demote/drop accounting; extend the
  eval dataset + baseline; update `specs/skills/review.md` contract-first; capture docs.

## Complexity Tracking

| Item | Why needed | Why the simpler path is insufficient |
|---|---|---|
| Declared architectural change to `specs/skills/review.md` (Principle II) | The feature alters the review skill's observable contract (consistency guarantee, yellow bar, discovery mode, pre-existing labeling, human-disposition authority). | Not editing the durable spec trips `scripts/spec_change_guard.py` and violates Constitution II. Editing it *after* the code inverts the source of truth. Contract-first + PR declaration is required, not optional. |
| ~5 new sibling modules instead of extending `review_runner.py` | The runner is already ~4,188 lines (7× the soft limit); consistency-critical logic must be unit-testable without the provider. | Appending worsens an oversized module and couples deterministic logic to the provider pipeline, blocking fast mock-free tests. |
| Open US7 posture (any commenter, incl. critical) + audit/guardrail | User's explicit decision, aligned with Constitution I ("human decides"). | A stricter default would contradict the user's choice; FR-036 attribution + FR-038 rubric guardrail keep it auditable/injection-resistant without overriding intent. An optional operator config guard is a future tightening. |

## Notes for /speckit-tasks

- Test-first for each deterministic helper (identity, reuse key, freeze, disposition parse):
  behavior tests only, no provider calls.
- Prompt changes MUST be paired with eval dataset cases (repeat-stability, pre-existing,
  disposition-honored, recall) so CI measures them — spec FR-022 / SC-007.
- Keep the three review prompt variants in sync via the shared `_partials` include.
- `specs/skills/review.md` edit is contract-first and must be **declared** in the PR body.
