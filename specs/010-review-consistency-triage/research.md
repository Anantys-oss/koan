# Phase 0 — Research & Decisions

All decisions below resolve the Technical-Context unknowns. Most were pre-resolved by the five
clarifications recorded in `spec.md` (§ Clarifications); this file records the *implementation*
choices that follow from them and the existing-code findings that ground them.

## D1 — Finding identity (spec FR-002; clarification Q1)

**Decision**: identity key = `file` + **tolerant code region** (anchor line snapped to a small
window, e.g. ±N lines / nearest changed hunk) + **semantic topic/category** (a normalized issue
class, not the model's title wording). Implemented in a new `review_identity.py`
(`finding_key(finding) -> str`), pure and unit-testable.

**Rationale**: exact line+title is brittle (reword/line-shift → false drift, defeating US1);
file-only is too coarse (collapses distinct issues). Tolerant region + semantic topic serves both
run-to-run stability and cross-perspective dedup (US3).

**Alternatives considered**: exact line+title (rejected: brittle); file+symbol (rejected: not all
findings sit in a named symbol — config/top-level); embedding similarity (rejected: nondeterministic,
adds a model dependency to a path that must be deterministic).

**Grounds**: today's reconciliation already matches prior findings heuristically in
`review_runner._reconcile_*`; extracting a single canonical key removes ad-hoc matching.

## D2 — Reuse short-circuit & request signature (FR-001; clarification Q2)

**Decision**: reuse the prior review verbatim iff **PR head SHA == prior head SHA AND
base/merge-base SHA == prior base SHA AND request signature matches**. Request signature =
{target PR, focus flags (`--architecture`/`--errors`/`--comments`/`--plan-url`), comprehensive-discovery
on/off}. Implemented in `review_reuse.py`; the reused post is marked as a reproduction (FR-006).

**Rationale**: base movement changes the effective diff, so head-only reuse could replay stale
findings (Q2). Signature prevents reusing a single-pass review as a comprehensive one (and vice
versa) — the FR-016 equivalence rule.

**Alternatives**: head-only (rejected: correctness hazard); diff-content hash (viable but
heavier; SHAs are already captured by `_fetch_pr_commit_shas`/`_fetch_pr_head_oid`). Keep SHA
comparison; revisit content-hash only if SHA churn proves noisy.

**Grounds**: `_fetch_pr_commit_shas` / `_fetch_pr_head_oid` already exist; the sidecar already
stores `prior_head_sha`. Add `prior_base_sha` + `request_signature` to the sidecar (D6).

## D3 — Re-review freeze on unchanged code (FR-003; clarifications Q4/Q5)

**Decision**: on re-derive (HEAD changed), compute the **incremental diff** (prior head SHA →
current head SHA). Partition candidate findings by `review_identity` against the prior set:
resolved-prior → suppress; matched-prior → recur; first-time on **changed** region → allow;
first-time on **unchanged** region → **suppress** unless severity `critical` (then surface with
`[Pre-Existing Issue]` prefix). Implemented in `review_reconcile.py` (extends today's
`_reconcile_review_after_reflection`/`_remap_findings_after_drop`).

**Rationale**: kills the exact "new complaints on code I didn't touch" whiplash (SC-011) while the
`critical` valve prevents silently withholding a real security miss.

**Coexist with pre-existing labeling (Q3)**: the freeze governs *whether* a first-time non-critical
finding on unchanged code surfaces (it does not); `[Pre-Existing Issue]` governs *how* pre-existing
findings that DO surface are presented. Freeze wins on re-review novelty.

**Alternatives**: absolute freeze incl. critical (rejected in Q4: unsafe); surface-all-labeled
(rejected in Q3: reintroduces late churn).

## D4 — Yellow-tier bar + pre-existing labeling (FR-008–014, FR-027–030; US2/US6)

**Decision**: two layers. (a) **Prompt rubric** in a shared `_partials/review-severity-rubric.md`
defines the Important bar (keep only clear blocker/real-harm; demote borderline; drop noise) and
the `[Pre-Existing Issue]` rule (non-critical predating changeset → `suggestion` + prefix; critical
→ keep severity + prefix). (b) **Deterministic post-pass** `review_triage.py` enforces the
invariants the prompt can't guarantee: re-derive `lgtm` from post-triage severities (already done
by `_build_verdict_body`/verdict finalization — reuse), apply a stable tiebreak for borderline
(deterministic, FR-011), and normalize/validate the `[Pre-Existing Issue]` prefix placement.

**Rationale**: prompt sets policy; Python guarantees the testable invariants (verdict-follows-
severity, determinism) so evals can't be flaked by model variance.

**Grounds**: `get_review_triage_config`, `get_review_verdict_config`, `get_review_calibration_config`
already exist; the reflection scorer + verdict finalizer already re-derive `lgtm`. This extends
rather than invents.

**No schema change**: severity stays `critical|warning|suggestion`; the label lives in the finding
title (FR-029). Confirmed against `review_schema.py` `_VALID_SEVERITIES`.

## D5 — Comprehensive discovery, opt-in (FR-015–021; US3; clarification Q3-specify)

**Decision**: a new `_partials/review-comprehensive-discovery.md` enumerating the **fixed**
perspective set (correctness, security, architecture, silent-failure, test-coverage) and the
merge/dedup/reconcile instruction; `{@include}`-d into `review.md` **only when**
`get_review_discovery_config().enabled` is true (default false). Merge/dedup reuses
`review_identity` (highest-justified severity + clearest explanation wins). Fail-open + bounded
(FR-019); partial coverage reported via the existing `⚠️ Partial review` path (FR-021).

**Rationale**: matches the user's "extra partial, config-gated, default-off" decision; keeps the
default path byte-identical (SC-008). Delivered as prompt guidance (the reviewing agent may use
sub-agents where the provider supports them) — no Python orchestration layer.

**Alternatives**: Python-orchestrated parallel provider passes (rejected per clarification —
heavier, and the user chose the prompt-partial route); per-perspective config toggles (rejected
per clarification — whole-mode on/off only).

## D6 — Review sidecar schema extension (persistence seam; US1/US7)

**Decision**: extend the existing `.review-findings/{owner}_{repo}_{pr}.json` (written by
`_write_review_findings_sidecar`, read by `_read_prior_findings_sidecar`) with: `base_sha`,
`request_signature`, per-finding `identity_key`, `pre_existing` flag, and a `dispositions[]` list
(kind, commenter, rationale-quote, target identity_key, timestamp). Reader stays fail-open
(missing/corrupt → treat as no prior review, FR-007).

**Rationale**: the sidecar is already the prior-review record; extending it (vs. a new store) keeps
Constitution III (file-first runtime state) and reuses atomic-write plumbing.

**Alternatives**: SQLite table (rejected: overkill, adds a store; sidecar is sufficient and already
present). GitHub comment as source of truth (rejected: parsing our own rendered comment is lossy).

## D7 — Human disposition parsing (FR-031–038; US7; clarifications Q-specify #6/#7)

**Decision**: `review_dispositions.py` reads the already-ingested PR comments (from the
`review-context` fetch) and, together with the `_partials/review-dispositions.md` prompt guidance,
classifies each into {dismiss, defer, retract, none}. Matching a disposition to a finding uses
inline-comment location or reply-threading → `review_identity`. **Any non-bot commenter** counts
(Q-specify #6); dispositions apply to **all severities incl. critical** (Q-specify #7). Every
resulting suppression/downgrade is **attributed** (commenter + quoted rationale) in the rendered
review (FR-036). Dispositions persist in the sidecar (D6), sticky by identity until retracted
(FR-037). A comment can dispose of a specific finding but cannot rewrite the rubric/verdict
(FR-038 injection guardrail — enforced by keeping rubric text prompt-fixed and treating comment
body as data, echoing the `review-repo-conventions.md` untrusted-content guardrail).

**Rationale**: reuses existing comment ingestion + the reply `action` mechanism
(`wont_fix`/`acknowledged`); no new fetch contract, no schema change. Aligns with Constitution I.

**Security note**: the open posture is the user's explicit choice; compensated by attribution
(never-silent) + rubric guardrail. Bot comments excluded (bot-filter already used elsewhere,
e.g. `find_bot_comment`). Documented risk + optional future config guard in spec Assumptions.

## D8 — Config keys (FR-014, FR-016, FR-020, FR-023)

**Decision**: add getters following the existing convention (`_get_config_with_overrides`,
per-project override):
- `get_review_consistency_config()` → `{ reuse_enabled: true, freeze_enabled: true }`
- `get_review_discovery_config()` → `{ enabled: false }` (default OFF — SC-008)
- `get_review_triage_config()` (exists) → extend with `important_bar` tuning + `pre_existing_label`
- `get_review_dispositions_config()` → `{ enabled: true, honor_critical: true, min_role: "any" }`
  (defaults encode Q6/Q7; `min_role` is the optional future tightening knob, default `"any"`).

**Rationale**: backward-compatible, fail-open to current behavior, per-project overridable — matches
the ~20 existing `get_review_*_config()` helpers.

## D9 — Evaluation strategy (FR-022; SC-007/009/012)

**Decision**: extend `koan/skills/core/review/evals/cases/` with: a **repeat-stability** case
(same fixture reviewed twice → identity-keyed overlap == 100% on blocking set), a **pre-existing**
case (base-code non-critical → green `[Pre-Existing Issue]`, no block), a **disposition** case
(comment "not a problem" → finding not re-raised, attributed), and a **recall** case (N seeded
issues → single pass surfaces all within coverage). Update `baseline.json`. Reuse `validate_review`
as the single source of truth (per the harness contract).

**Rationale**: the spec's contract requires behavior changes to be measured; CI `fast` scorer +
live mode catch regressions (drift returns / yellow bar loosens / recall drops).

## D10 — Contract-first spec update (FR-023; Constitution II)

**Decision**: `specs/skills/review.md` is edited **first/with** the code to add the new invariants
(consistency guarantee + reuse key, freeze, yellow bar, opt-in discovery, `[Pre-Existing Issue]`,
human-disposition authority + guardrails). The PR **declares** the architectural change
(PR-template box). `scripts/spec_change_guard.py` enforces this.

**Rationale**: durable contracts constrain code, not the reverse (Constitution II;
`docs/design/spec-changes-are-architectural.md`).
