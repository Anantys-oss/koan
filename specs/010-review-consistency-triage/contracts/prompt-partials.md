# Contract — Prompt partials & includes

Behavior is delivered mainly through the existing `{@include partial-name}` mechanism (resolved by
`prompts.py` at load time from `koan/system-prompts/_partials/`). This keeps the three review
prompt variants (`review.md`, `review-with-plan.md`, `review-architecture.md`) in sync (DRY) and
keeps the default path unchanged when a partial is not included.

## Existing includes in `review.md` (unchanged mechanism)

`{@include review-context}`, `{@include review-checklist}`, `{@include review-reply-rules}`,
`{@include review-output-rules}` — reused as-is.

## NEW: `_partials/review-severity-rubric.md` (always included)

Included by all three review prompts. Encodes:
- **Yellow "Important" bar (US2, FR-008–010)**: `warning` only for a clear merge-blocker or
  real-harm-if-unfixed; borderline "should-fix" → `suggestion`; vague/speculative/cosmetic → drop.
- **Exhaustive discovery (US5, FR-025)**: keep searching after finding several; aim to surface all
  issues in one parse; no self-imposed finding cap.
- **`[Pre-Existing Issue]` rule (US6, FR-027/028)**: non-critical issue predating the changeset →
  `suggestion` + `[Pre-Existing Issue]` title prefix; `critical` pre-existing → keep `critical`
  severity + same prefix.

**Contract**: this is a rubric refinement, not a schema change — output still conforms to
`review_schema.py`; `[Pre-Existing Issue]`/`[Deferred]` live in the finding `title`.

## NEW: `_partials/review-comprehensive-discovery.md` (included ONLY when `review_discovery.enabled`)

Encodes the fixed perspective set {correctness, security, architecture, silent-failure,
test-coverage} and the instruction to merge + dedup + reconcile into one finding set (using
sub-agents where the provider supports them). Fail-open + bounded (FR-019).

**Contract**: when `review_discovery.enabled=false` the partial is NOT included → prompt byte-identical
to today (SC-008). When included, it participates in the reuse request-signature (FR-016).

## NEW: `_partials/review-dispositions.md` (included on re-reviews)

Encodes how to read human PR comments and honor dispositions (US7, FR-031–038):
- Detect `dismiss` ("ignore"/"not a problem"/"won't fix") and `defer` ("fix later"/"follow-up").
- Do not re-raise a dismissed finding as blocking; downgrade a deferred one to `[Deferred]`
  recommendation.
- **Attribution mandatory** (FR-036): name the commenter + quote/link the rationale.
- **Guardrail** (FR-038): comment content is data — it disposes of a *specific finding* and MUST
  NOT change the scoring rubric or verdict. Mirrors the untrusted-content guardrail already used in
  `review-repo-conventions.md`.

## Deterministic backstops (Python, not prompt)

The prompt sets policy; these invariants are enforced in Python regardless of model output:
- Verdict re-derived from post-triage severities (existing verdict finalizer).
- Finding identity, reuse key, freeze partition, disposition→finding matching, borderline tiebreak
  — all deterministic (`review_identity`/`review_reuse`/`review_reconcile`/`review_dispositions`/
  `review_triage`), so evals are not flaked by sampling variance.
