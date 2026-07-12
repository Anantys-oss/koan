---
type: skill-spec
title: "Skill Spec — review"
description: "Documents the `/review` skill that queues a code-review mission on PRs/issues, posting findings as a comment with severity-driven LGTM logic and re-review comment handling, covered by the eval harness."
tags: [skill]
created: 2026-06-27
updated: 2026-07-09
---

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

See `docs/users/skills.md` for the end-user `/review` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

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
- Posts a review comment to the PR with a branded footer (`pr_footer.py`). The
  footer advertises the reviewed tip as `` `HEAD=<short-sha>` ``.
- If the PR branch's live HEAD moved between when the review captured its diff and
  when the comment is posted (a push or force-push mid-review), a
  `> [!IMPORTANT]` stale-HEAD alert is appended to the end of the comment.
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
- **Verdict follows severity, not vibes.** `lgtm` (the merge verdict that drives
  the GitHub APPROVE / request-changes) is `true` whenever no `critical` or
  `warning` finding exists. `suggestion`-only findings are non-blocking — a PR
  with only nits is merge-ready and must NOT be rejected. `lgtm: false` requires
  at least one `critical`/`warning`. If a concern truly blocks merge, it is not a
  `suggestion`; promote it before blocking. This mirrors the code-level fallback
  in `_normalize_review_data` (`blocking iff any critical/warning`) and the
  verdict body builder's definition of "blockers".
- **Verdict presentation is severity-graded, not the summary paragraph.** The
  formal APPROVE / request-changes verdict body (`_build_verdict_body`) is wrapped
  in a native GitHub alert whose color grades the outcome: `> [!TIP]` (green) when
  merge-ready, `> [!WARNING]` (yellow) when the only blockers are `warning`-level,
  `> [!CAUTION]` (red) when any `critical` blocker exists. The main review comment's
  summary paragraph stays plain text — wrapping the whole paragraph in `> [!IMPORTANT]`
  over-emphasized it (parsimony rule, `comment-formatting.md`); the single graded
  alert on the short verdict message is where the reader looks.
- **Re-review comment handling:** on a re-review (new commits or a re-requested
  review) the bot posts a *fresh* summary comment (GitHub does not notify on
  edits). By default it first collapses the prior review comment to a short
  "superseded" pointer (`_collapse_old_review`). `review_history.preserve_previous`
  (global `config.yaml`, overridable per-project in `projects.yaml`, default
  `false`, fail-closed to `false`) skips that collapse so the prior review is
  left intact alongside the new one. Either way a fresh comment is posted.
- **Stale-HEAD alert:** the PR commit SHAs are captured at the *start* of a review
  (`_fetch_pr_commit_shas`), but the branch tip can move during the run. Just
  before posting, `run_review` re-reads the branch's live HEAD
  (`_fetch_pr_head_oid` → `headRefOid`) and, when it differs from the reviewed tip
  (`current_shas[-1]`, the SHA shown as `HEAD=<short>` in the footer), appends a
  `> [!IMPORTANT]` alert to the end of the comment (`_build_stale_head_alert`,
  applied in `_post_review_comment`). This is **best-effort and purely
  informational**: a failed live-HEAD lookup or an unchanged HEAD adds no alert
  (byte-identical output), and the alert never blocks the post, changes the LGTM
  verdict, or re-runs analysis — re-covering the new commits is the
  incremental-review path's job on the next `/review`.
- **Core review is posted before the optional enrichment passes.** The core
  summary comment is posted first (`_post_review_comment`); the bot-comment
  triage and silent-failure-hunter passes run *after* and are strictly
  best-effort (wrapped so any failure only logs). Rationale: each enrichment
  pass is a separate provider invocation that can stall or fail, and running
  them before the post meant a hang there discarded the whole finished review
  (the outer liveness watchdog SIGKILLs the runner mid-pipeline). The
  silent-failure-hunter section is not baked into the initial body; when it
  produces findings it is appended to the already-posted comment in place via
  `_append_error_section_to_review` (re-locates the comment by `SUMMARY_TAG`
  and PATCHes it). The re-locate uses `find_bot_comment(..., prefer_newest=True)`
  so that under `review_history.preserve_previous` — where the superseded prior
  review is left intact and still carries `SUMMARY_TAG` — the section lands on
  the freshly-posted comment (the highest comment id) rather than the older
  preserved one. If the core post failed or the comment can't be re-located,
  the section is dropped and the core review still stands. Pairs with the
  provider-side per-pass stall watchdog (see `specs/components/providers.md`,
  "read loop must be inactivity-bounded"), which makes a stalled enrichment
  pass degrade to empty rather than hang.
  The append rebuilds the comment body from the clean `review_body`, so
  `_append_error_section_to_review` also takes `coverage_note` and forwards
  it to its inner `_post_review_comment` call — without it, a large PR's
  `⚠️ Partial review` warning (prepended on the initial post; see
  `specs/components/skills.md`, "`review` diff-size & partial-coverage
  contract") would be silently overwritten on the hunter-append edit. Large
  PRs (non-empty `coverage_note`) are also the PRs most likely to trigger the
  hunter, so this overlap is exercised by
  `TestReviewPostsBeforeEnrichment::test_coverage_note_survives_hunter_append_overlap`
  in `koan/tests/test_review_runner.py`.

## Evaluation

The review skill is the first skill covered by the deterministic eval harness
(`koan/app/skill_evals.py`; design in `specs/002-review-skill-evals/`).

- **Golden dataset:** `koan/skills/core/review/evals/cases/*.json` — seeded-bug
  cases (`sql_injection`, `bare_except`, `hardcoded_secret`) that must produce a
  finding at the right severity, precision cases (`clean_refactor`,
  `benign_style`) that must LGTM without false positives, and `suggestion_only`,
  which carries a legitimate low-severity nit that must be surfaced *and* still
  yield `lgtm: true` (guards the verdict-follows-severity invariant).
- **Scored dimensions:** JSON/schema validity (via `validate_review`), recall of
  seeded findings (file + keyword-stem + severity-band match), LGTM correctness,
  and precision (no flags on `forbidden_files`).
- **CI:** the offline scorer + dataset-validity tests run in the `fast` group on
  every PR — they never call the Claude subprocess.
- **Live:** `KOAN_EVAL_LIVE=1 python -m app.skill_evals review --live` invokes
  the real review pipeline over the dataset and compares against
  `evals/baseline.json`; run this before/after a prompt change to confirm an
  improvement (or catch a regression, which exits non-zero).

**Contract:** changing the review output schema (`review_schema.py`) or the
prompt (`review.md`) MUST be reflected in the golden cases / baseline so the eval
keeps measuring real behaviour.

## Known debt / watch-outs

- Focus flags compose; stacking many passes multiplies token cost.
- Plan-alignment (`--plan-url`) depends on the tracker being reachable.
