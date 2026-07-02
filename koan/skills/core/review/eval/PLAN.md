# Review skill — eval plan

> Status: **Tier 1 shipped** (CI-safe). Tier 2 **designed**, gated behind an API key,
> not run in CI. This document is the design rationale the mission asked for
> ("challenge the problem, come with a good plan before implementation").

## The problem, stated honestly

The `/review` skill is **prompt-driven**. Its product is *stochastic LLM output*, not
deterministic code. So "an eval for the review skill" can mean two very different things:

1. **Review-contract eval** — does the skill's scaffolding still hold? (prompt integrity,
   output schema, cross-field consistency)
2. **Review-quality eval** — does the review actually catch the bugs it should?

These have **opposite CI profiles**, and that tension is the whole design.

## The CI wall

`.github/workflows/tests.yml` runs the suite with **no Claude API token**
(`KOAN_TELEGRAM_TOKEN=fake-token-for-ci`, every model call mocked). So any eval that
*invokes a model to review a diff* is **out for CI** — it needs a token, costs money, and
is non-deterministic. The mission says "ideally run in CI **if possible**." The honest
answer: the *quality* eval isn't possible in CI without a token; the *contract* eval is.

## What regressions actually bite a prompt skill?

Ranked by likelihood × current protection:

| Regression | Likelihood | Currently protected? |
|---|---|---|
| **Prompt drift** — someone edits `review.md`, deletes the output schema / severity table / "JSON-only" rule, or breaks an `{@include}` partial. Reviews silently degrade. | **High** | **No** — every test still passes. |
| **Schema-validator drift** — `validate_review()` loosened → malformed reviews pass → garbage posted to PRs. Or tightened → valid reviews rejected. | Medium | Partially (`test_review_schema.py` covers structural accept/reject) |
| **Semantic-invariant break** — a review with `critical` findings ships `lgtm: true`; dangling `finding_refs`. Schema can't express cross-field rules. | Medium | **No** |
| **Quality regression** — review stops catching a planted bug after a prompt change. | Medium | **No** (needs a model) |

**Prompt drift is the #1 risk and has zero coverage today.** That is the gap Tier 1 closes.

## Tier 1 — CI-safe contract eval (shipped)

Plain pytest, no model, runs in the existing `fast` CI group. Three dimensions:

### A. Prompt-contract eval (the high-value one)
Load each review prompt through the real `load_skill_prompt()` and assert the load-bearing
contract survives an edit:
- All `{@include}` directives resolve to **non-empty** content (a renamed/deleted partial
  is caught).
- The prompt still contains the **output schema**, the **severity-calibration table**, and
  the **"single valid JSON object"** directive.
- The severity vocabulary written in the prompt matches the code's `_VALID_SEVERITIES`.

This is the regression net for prompt edits. Pattern mirrors the existing
`TestDefaultPlaceholdersAlwaysResolved` walk in `test_prompts.py`.

### B. Golden-output anchors
Curated, hand-authored review JSON fixtures that embody *"this is a well-formed review"*:
an LGTM review, a review with mixed severities, one with `comment_replies`. Each must pass
both `validate_review()` **and** the semantic eval with no violations. They are regression
anchors: any schema or invariant change that breaks a golden output fails CI.

Hand-authored (not model-captured) deliberately — a model capture encodes "what the model
happens to produce" (unstable, needs a token to regenerate); a hand-authored fixture
encodes the *contract* we want to assert (stable, deterministic, token-free).

### C. Semantic-invariant + adversarial eval
`review_eval.evaluate_review()` layers cross-field rules `validate_review()` can't express:
- Any `critical`/`warning` finding ⇒ `lgtm` must be `false`.
- `finding_refs` must be in range of `file_comments`.
- `file_comments` empty ⇒ `lgtm` should be `true` (an LGTM with phantom comments is wrong).
- Severities all valid (belt-and-suspenders over the schema check).

Fed an **adversarial corpus** of *schema-valid-but-semantically-broken* reviews — these pass
`validate_review()` but must be flagged by `evaluate_review()`. This guards the eval itself
against becoming too lax, and it is the **seed of a real scoring function**.

## Tier 2 — model-driven quality eval (designed, gated)

The actual quality eval. Local/manual (or nightly), **never in CI**:

- A handful of **golden diffs**: one with a planted SQL-injection, one with a planted
  off-by-one, one **clean** diff (for false-positive rate).
- Run the review prompt against a real model, then score by a **deterministic rubric**:
  - Did it flag the planted file? At the right severity? (recall)
  - Did it stay quiet on the clean diff? (precision / false positives)
  - Is the output valid per `validate_review()` + `evaluate_review()`?
- Gated behind `KOAN_EVAL_MODEL` (or an API key); `make eval-review` runs it; results
  written to a markdown report under `eval/results/` so iterations are comparable over time.

`evaluate_review()` is shared between tiers: Tier 2 calls it on live model output, Tier 1
calls it on fixtures. Building it now is forward-compatible, not throwaway.

## Why this shape (challenged)

- **"Just add more `test_review_schema.py` cases"** — no. That tests structural
  validation, which is already covered. Prompt drift and semantic invariants are the
  unprotected gaps; that's where the value is.
- **"Run the model in CI"** — no. No token, non-deterministic, costs money, slow. The
  mission's "if possible" rules it out for CI; Tier 2 is the honest home for it.
- **"LLM-as-judge in CI"** — same problem, plus judge variance. Defer to Tier 2.
- **"Snapshot a model output and assert it in CI"** — catches drift in the *committed*
  snapshot, not live regressions from prompt edits, and regeneration needs a token.
  Lower value than Tier 1; skip for the starter.

Tier 1 is deliberately small, CI-runnable, and protects the regressions that actually
break a prompt skill. Tier 2 is the documented next step when someone wants to measure
true review quality.
