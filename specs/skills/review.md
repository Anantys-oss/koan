# Skill Spec — `review`

## Command(s)

- **Primary:** `/review [--now] <pr-or-issue-url> [more urls] [context] [flags]`
  or `/review <repo-url> [--limit=N]`
- **Aliases:** `rv`
- **Group:** `code`

## Purpose

Queue a code-review mission for one or more GitHub PRs/issues. The agent reviews the
diff and posts findings as a review comment. The default review can be sharpened with
focus passes (architecture, silent-failure hunting, comment quality, plan alignment).

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR/issue URL(s) | command arg | yes (or repo URL) | multiple allowed; parsed by `github_url_parser` |
| repo URL + `--limit=N` | command arg | alt form | batch-review N open PRs |
| `--now` | flag | no | queue at top |
| `--architecture` | flag | no | SOLID/layering focus |
| `--errors` | flag | no | silent-failure-hunter pass |
| `--comments` | flag | no | comment-quality pass |
| `--plan-url <issue-url>` | flag | no | check PR against its plan |
| `--force` | flag | no | review even if closed/merged |
| trailing context | command arg | no | extra reviewer guidance |

## Outputs / side effects

- Queues a review mission (one per URL); the agent loop runs it.
- Posts a review comment to the PR with a branded footer (`pr_footer.py`).
- Review prompt is enriched with `{ISSUE_CONTEXT}` from `issue_tracker/enrichment.py`.

## Error cases

| Condition | Behavior |
|---|---|
| invalid/missing URL | reply with usage |
| closed/merged target | skipped unless `--force` |
| unresolved project | alias resolution then skip if unknown |

## Integration hooks

- **Handler:** `handler.py`. **GitHub:** `github_enabled` + `github_context_aware`.
- **Combo member:** part of `review_rebase` (`/rr`) and `ultrareview`.
- **Async:** runs as a queued agent-loop mission.

## Invariants

- Multi-URL queues preserve order via a single atomic locked insert.
- Findings are advisory comments — `/review` never merges or pushes code.

## Known debt / watch-outs

- Focus flags compose; stacking many passes multiplies token cost.
- Plan-alignment (`--plan-url`) depends on the tracker being reachable.

## Eval

`/review` is prompt-driven, so its output is stochastic LLM text — traditional
unit tests can't capture review *quality*. The eval is therefore split into two
tiers (full rationale in `skills/core/review/eval/PLAN.md`):

- **Tier 1 — CI-safe contract eval (shipped).** Plain pytest, no model, runs in
  the `fast` CI group via `tests/test_review_eval.py`. Three dimensions:
  1. **Prompt-contract** — every review prompt keeps its load-bearing structure:
     all `{@include}` partials resolve, and the JSON-output prompts still carry
     the `valid JSON` directive + full severity vocabulary when rendered. This is
     the regression net for prompt drift (the #1 unprotected risk).
  2. **Golden-output anchors** — curated fixtures in
     `skills/core/review/eval/fixtures/` must stay schema-valid AND pass the
     semantic eval. Anchors that make "confirm improvements over iterations"
     meaningful.
  3. **Semantic invariants** — `app/review_eval.evaluate_review()` layers
     cross-field rules the schema can't express (blocking finding ⇒ `lgtm:false`,
     `finding_refs` in range, empty `file_comments` + not-LGTM ⇒ warning). Fed an
     adversarial corpus of schema-valid-but-broken reviews to prove it flags what
     `validate_review()` alone misses.
- **Tier 2 — model-driven quality eval (designed, not built).** Golden diffs
  (planted bug, clean diff for false-positive rate) run through the review prompt
  against a real model, scored by a deterministic rubric. Needs an API key →
  never runs in CI (`tests.yml` has no token); invoked manually via a future
  `make eval-review`. Shares `evaluate_review()` with Tier 1.

Invariant contract: `evaluate_review()` returns `passed=True` only when a review
is both structurally valid (`validate_review`) and semantically consistent. Any
loosening of the invariants must update the golden fixtures or the eval fails.
