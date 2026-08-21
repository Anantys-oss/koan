---
type: skill-spec
title: "Skill Spec — review"
description: "Documents the `/review` skill that queues a code-review mission on PRs/issues, posting findings as a comment with severity-driven LGTM logic and re-review comment handling, covered by the eval harness."
tags: [skill]
created: 2026-06-27
updated: 2026-08-19
---

# Skill Spec — `review`

## Command(s)

- **Primary:** `/review [--now] <pr-or-issue-url> [more urls] [context] [flags]`
  or `/review <repo-url> [--limit=N]`
- **Aliases:** `rv`, `rereview`, `re_review`
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
| `--force` | flag | no | review even if closed/merged or pause-label is present |
| trailing context | command arg | no | extra reviewer guidance |
| `.koan/config.yaml` `review.always_check` | target repo file | no | file globs pinned into the reviewed diff (see Invariants) |

## Outputs / side effects

- Queues a review mission (one per URL); the agent loop runs it.
- Posts a review comment to the PR with a branded footer (`pr_footer.py`). The
  footer advertises the reviewed tip as `` `HEAD=<short-sha>` ``.
- If the PR branch's live HEAD moved between when the review captured its diff and
  when the comment is posted (a push or force-push mid-review), a
  `> [!IMPORTANT]` stale-HEAD alert is appended to the end of the comment.
- When the incremental-review check finds no new commits since the last review
  (all current SHAs already reviewed, no re-request), the run posts a short
  `> [!NOTE]` (blue) comment telling the requester the review was skipped because
  the code is unchanged — instead of skipping silently. The user asked (via
  `/review` or an @mention) and gets an answer rather than only the thumbs-up ack.
- Review prompt is enriched with `{ISSUE_CONTEXT}` from `issue_tracker/enrichment.py`.

## Error cases

| Condition | Behavior |
|---|---|
| invalid/missing URL | reply with usage |
| closed/merged target | skipped unless `--force` |
| pause-label present | success-with-skip unless `--force` (see invariant) |
| unresolved project | alias resolution then skip if unknown |

## Integration hooks

- **Handler:** `handler.py`. **GitHub:** `github_enabled` + `github_context_aware`.
- **Combo member:** part of `review_rebase` (`/rr`) and `ultrareview`.
- **Async:** runs as a queued agent-loop mission.

## Invariants

- Multi-URL queues preserve order via a single atomic locked insert.
- Findings are advisory comments — `/review` never merges or pushes code.
- **Read-only is enforced by a positive allowlist, not by omission.**
  `review_mode` is a member of `app.provider.READ_ONLY_ROLES`, so every review
  pass is restricted to `READ_ONLY_TOOLS` via `--tools` and *additionally* denies
  `SIDE_EFFECT_TOOLS`. Passing only
  `["Read","Glob","Grep"]` to `allowed_tools` does **not** withhold the rest:
  `--allowedTools` pre-approves invocations, it does not restrict which tools
  exist — the CLI's own reference says *"To restrict which tools are available,
  use `--tools` instead"*. Verified on Claude CLI 2.1.234: under
  `--allowedTools Read,Glob,Grep` a `Bash` call still succeeds, while under
  `--disallowedTools Bash` it is blocked. Enforcement strength is
  provider-dependent: Claude and ollama-launch deny by name; Codex ignores tool
  arguments entirely and is instead pinned to `--sandbox read-only`. Every other
  adapter declares neither and is refused — for reasons that differ per provider
  (`cline`/`haze` cannot express a restriction at all; `copilot`'s adapter does
  not wire up the CLI's `--deny-tool`; `grok` emits tool flags but Kōan has not
  measured that they withhold, and it auto-approves unconditionally). See
  `specs/components/providers.md` for the per-provider detail — the predicate is
  a claim about Kōan's adapter, not about the vendor CLI. A read-only role
  also refuses to honour `skip_permissions`, because
  `--dangerously-skip-permissions` / `--dangerously-bypass-approvals-and-sandbox`
  would bypass the very denial that makes the role read-only.
- **A review runs against a disposable checkout of the PR head, never the
  operator's working tree.** Missions are dispatched after
  `git_prep.prepare_project_branch`, which checks out the *base* branch and
  hard-resets it to `<remote>/<base>` — so the reviewed code was historically
  never on disk, while the prompts told the model to verify against "the
  checked-out code". `/review` therefore creates a detached worktree pinned to
  GitHub's live `headRefOid`, verifies the SHA twice (after fetch, and after
  checkout), asserts the tree is clean, and removes it afterwards.
- **A failed pin FAILS the review; it never falls back to `project_path`.**
  Reviewing the wrong tree and reporting the findings as fact is the defect this
  invariant exists to remove — a silent fallback reproduces it with a warning
  nobody reads. A failed review is visible and retriable; a wrong review is
  neither.
- **Prompts and skills come from the running Kōan, never from the reviewed
  tree.** `skill_dir` resolves from the installed module, so a pull request
  cannot rewrite the prompt that judges it.
- **The review shell is gated, and the gate fails to "no shell".** `Bash` is in
  `READ_ONLY_TOOLS` but is *never* placed in `--allowedTools`; each command is
  adjudicated by a `PreToolUse` hook supplied per-invocation via `--settings`.
  If that hook does not load for any reason, `Bash` is un-pre-approved and
  headless mode denies it. The failure mode is therefore losing the shell, not
  gaining an unguarded one — measured on Claude CLI 2.1.235, where a hook
  returning `allow` let a command run with no `Bash` entry in `--allowedTools`,
  and one returning `deny` blocked it with the reason surfaced and no side
  effect.
- **The shell is read-only by policy, one command per call.** Shell operators —
  redirection, pipes, chaining, command substitution — are rejected on the raw
  string before parsing, because `shlex` is a word splitter and not a shell
  parser: it happily returns `['git','log','>','x']`, which a token-level check
  would pass and Bash would then execute as a redirect. Pipelines are rejected
  in v1 by decision, not oversight: every stage would need validating and a
  `tee`/`sh`/`dd` in stage two defeats stage one. `koan/app/review_bash_guard.py`
  is the enumeration; this spec governs the policy.
- **An unenforceable review provider is REFUSED, not downgraded.** If the
  resolved `review_mode` provider (or the `cli.fallback` it swapped to) can
  enforce neither mechanism, `build_full_command` raises
  `ReadOnlyUnenforceable` and the review fails with an actionable configuration
  error naming the provider and `cli.review_mode`. `/review` never executes
  against an unenforceable provider — a review that could write to the live
  project clone is worse than no review, and the user-facing docs promise the
  boundary unconditionally. Operators on `cline`/`haze`/`copilot`/`grok` must pin
  `cli.review_mode` to `claude`, `codex`, or `ollama-launch`; the refused
  providers stay available for every other role. This covers the `/review` skill, which runs
  through `run_command_streaming`. It does **not** cover the separate
  `autonomous_mode == "review"` mission path in
  `mission_runner.build_mission_command` — see `specs/components/providers.md`.
- **Finding IDs span the complete review.** Displayed IDs start at 1 and
  increment through rendered severity order (Blocking, Important, Suggestions),
  then continue through Silent Failure Analysis findings. IDs never restart at
  a section boundary. Checklist references use these global IDs, letting a
  requester identify any specific finding without also naming its section.
- **Repo-level `always_check` pinning.** A target repo's optional
  `.koan/config.yaml` may set `review.always_check` to a list of file globs; any
  changed file whose path or basename matches is **pinned** so diff-size reduction
  includes it ahead of budgeted files and never silently drops it, and it does not
  appear in the `⚠️ Partial review` coverage note when included. Absent/empty/malformed
  config is a byte-identical no-op. Pinning reorders inclusion only (never raises the
  budget) and changes **neither the review schema nor the prompt templates**, so the
  eval golden dataset/baseline are unaffected. Contract:
  `specs/components/skills.md` → "`review` diff-size & partial-coverage contract" and
  "Repo config file (`.koan/config.yaml`)". User docs: `docs/users/koan-md.md`.
- **Pause label:** When `get_review_pause_label()` is non-empty and the PR
  carries that exact label, `run_review` returns success-with-skip **before**
  `fetch_pr_context`, prompt build, or any provider invocation. `force=True`
  / `--force` bypasses. Does not apply to `run_private_review`. Empty config
  (`review_pause_label: ""`) disables the check entirely. Default label name
  is `PauseReview`.
- **Verdict follows severity, not vibes.** `lgtm` (the merge verdict that drives
  the GitHub APPROVE / request-changes) is `true` whenever no `critical` or
  `warning` finding exists. `suggestion`-only findings are non-blocking — a PR
  with only nits is merge-ready and must NOT be rejected. `lgtm: false` requires
  at least one `critical`/`warning`. If a concern truly blocks merge, it is not a
  `suggestion`; promote it before blocking. Schema validation rejects a supplied
  verdict that contradicts the finding severities, and post-reflection
  finalization derives the verdict from the reconciled finding list again.
- **Valid reflection filtering is authoritative.** The reflection pass carries
  retained original finding indices into final reconciliation. When both the
  reflected finding array and retained-index array are valid and internally
  consistent, reconciliation retains exactly those findings. It remaps checklist
  references to the retained array, removes a failed checklist entry when all of
  its referenced evidence was filtered, and derives `lgtm` from the retained
  severities. It does not restore a rejected finding merely because the primary
  checklist referenced it or because filtering changes a primary blocking verdict
  to merge-ready.

  Reconciliation remains fail-open when reflection metadata is malformed or
  inconsistent. Both metadata values must be lists; every retained index must be
  a unique, non-boolean integer in range; the arrays must have equal lengths; and
  each reflected finding must match the original finding at its retained index.
  If any check fails, the primary review is returned unchanged. Schema validation
  continues to reject a contradictory verdict before anything is posted. As a
  defensive backstop, `_build_verdict_body` logs an inconsistency and submits an
  empty verdict body rather than failing after the review comment was posted.
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

### Consistency, triage & human dispositions (spec 010)

These invariants make repeated reviews stable, keep the blocking tier trustworthy,
and let humans close the loop. All are fail-open (a missing input degrades to the
prior single-pass behaviour) and the consistency-critical decisions are made in
deterministic Python (`review_identity`, `review_reuse`, `review_reconcile`,
`review_triage`), not in model output.

- **Finding identity (FR-002).** A finding's identity key is `file` + a tolerant
  region bucket + a semantic category (`review_identity.finding_key`) — independent
  of exact line numbers and title wording. `same_finding` adds ±line tolerance for
  cross-run matching. The sidecar stamps each persisted finding with its key.
- **Reuse on unchanged head+base (FR-001).** When the PR head **and** base
  (merge-base) SHA are both unchanged since the prior review **and** the request is
  equivalent (same focus flags + comprehensive-discovery setting — the
  `request_signature`), `run_review` reproduces the prior review instead of
  re-deriving it (`review_reuse.should_reuse`), rather than re-rolling a fresh set.
  Base movement defeats reuse (the effective diff changed). Reuse is
  distinguishable and never a silent no-op; a missing/non-equivalent prior record
  falls back to a fresh review. Gated by `review_consistency.reuse_enabled` (default
  on). The prior-review sidecar persists `base_sha` + `request_signature` for this.
- **Re-review freeze (FR-003).** On a re-review (head moved), a **first-time
  non-critical** finding on a **file unchanged since the prior review** is
  **suppressed** — the "review whiplash" case (new complaints on code the author did
  not touch). Recurring prior findings and findings in changed files are unaffected.
  A `critical` is the sole exception: it still surfaces, prefixed
  `[Pre-Existing Issue]`. Freeze is file-level and fail-open (`review_reconcile.compute_freeze`;
  the runner applies `_remap_findings_after_drop`). Gated by
  `review_consistency.freeze_enabled` (default on).
- **Yellow-tier bar (FR-008–010).** The `warning` (🟡 Important) tier — a *blocking*
  severity — is reserved for issues that clearly block merge or risk real harm;
  borderline "should-fix" items are `suggestion`s; vague/speculative/cosmetic noise
  is dropped. Set by the shared `review-severity-rubric` prompt partial ({@include}-d
  by `review.md` + `review-with-plan.md`; the markdown architecture prompt carries an
  equivalent Rules bullet). The bar is prompt-fixed at strict (not runtime config).
- **Exhaustive single-pass (FR-025).** The default prompt aims to surface every
  genuine issue in one pass (no finding cap), so later reviews have less to add;
  higher recall still flows through the same bar (no blocking-set inflation).
- **Pre-existing labeling (FR-027/028).** A finding the reviewer judges to predate
  the changeset carries the `[Pre-Existing Issue]` title prefix; if non-critical it
  is forced to `suggestion` (non-blocking), if `critical` it keeps its severity.
  Detection is the reviewer's semantic call (the prefix); `review_triage.enforce_pre_existing`
  enforces the severity rule deterministically and re-derives `lgtm`. Coexists with
  the freeze — the freeze runs first, so "freeze wins" on unchanged code (FR-030).
- **Human dispositions (FR-031–038).** On a re-review the reviewer honors human PR
  comments that **dismiss** ("ignore"/"not a problem" → not re-raised as a blocker)
  or **defer** ("fix later" → non-blocking `[Deferred]` recommendation) a finding.
  Any non-bot commenter, all severities including a human-dismissed `critical` ("the
  agent proposes, the human decides"). Every comment-driven suppression/downgrade
  MUST be attributed (commenter + quoted rationale) — never silent. A comment
  disposes of a *specific finding* only; it MUST NOT rewrite the severity rubric or
  verdict (injection guardrail). Stickiness + retraction come from re-reading the
  live comment thread each review (no separate store). Guidance is injected via the
  config-gated `{DISPOSITIONS}` slot (`review_dispositions.enabled`, default on — the
  kill-switch for the open posture); `review_triage.enforce_deferred` enforces the
  `[Deferred]` downgrade deterministically.
- **Opt-in comprehensive discovery (FR-015–021).** When `review_discovery.enabled`
  (default **off**), the review prompt gains the `review-comprehensive-discovery`
  guidance: review from a fixed perspective set (correctness, security, architecture,
  silent-failure, test-coverage), merge findings into one deduplicated set, same bar
  and verdict. Off → the prompt carries zero discovery content (byte-identical
  single-pass path). Part of the reuse `request_signature`, so a single-pass review
  is never reused as a comprehensive one.

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
