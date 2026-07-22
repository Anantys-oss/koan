---
type: skill-spec
title: "Skill Spec — rebase"
description: "Documents the `/rebase` skill that rebases a PR onto its current base by default and, with `--fix` (or any trailing context), also addresses review feedback, including its already-solved detection JSON scored by the eval harness."
tags: [skill]
created: 2026-06-27
updated: 2026-07-22
---

# Skill Spec — `rebase`

## Command(s)

- **Primary:** `/rebase [--now] [--fix] <pr-url> [context]`
- **Aliases:** `rb`
- **Group:** `pr`

## Purpose

Rebase a PR onto its current base — the standing workflow for keeping a Kōan PR
current and merge-ready. **By default `/rebase` performs only the rebase**
(rebase onto the base branch, resolving conflicts). The review-feedback leg
(read PR comments and apply requested changes) is **opt-in via `--fix`** — a
plain `/rebase` no longer applies feedback.

`--fix` is **implied by any trailing text after the URL** (a focus area or a
severity keyword), so `/rebase <url> address the auth bug` and
`/rebase <url> critical` both address feedback; only a bare `/rebase <url>`
rebases without touching feedback. `/fix` on a PR URL redirects here **with
`--fix`**.

See `docs/users/skills.md` for the end-user `/rebase` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR URL | command arg | yes | parsed by `github_url_parser` |
| `--now` | flag | no | queue at top |
| `--fix` | flag | no | also address review feedback; implied by any trailing context |
| trailing context | command arg | no | threaded into the queued mission; implies `--fix` |

## Outputs / side effects

- Queues a rebase mission (`model_key: mission`); runs via `rebase_pr.py`.
- Updates the PR branch (force-push with multi-account token resolution if needed).
- Commit messages shaped by `commit_conventions.py`.

## Error cases

| Condition | Behavior |
|---|---|
| invalid PR URL | reply with usage |
| force-push 403 (fork owned by other account) | recovery via `claude_step._force_push` using `gh auth token --user <owner>` |

## Integration hooks

- **Handler:** `handler.py` (also the redirect target of `fix`, which injects `--fix`).
- **GitHub:** `github_enabled` + `github_context_aware`.
- **Combo:** second leg of `review_rebase` (`/rr`), which passes `--fix` so the
  rebase leg addresses the review it just generated (`sub_commands: [review,
  "rebase --fix"]`).

## Invariants

- Post-URL context must thread into the queued mission and feedback prompt as a
  fenced explicit user request.
- Multi-account pushes resolve the remote owner's token; tokens redacted in logs.
- **Feedback leg is opt-in.** A bare `/rebase` rebases only; the feedback leg
  runs only when `--fix` is present or trailing text follows the URL. Callers
  that rely on feedback (`/fix` on a PR, `/rr`, autoreview) must pass `--fix`.
  The single decision point is `skill_dispatch._build_rebase_cmd`; the runner
  gates step 4 on `apply_feedback = fix or _FEEDBACK_ON_BY_DEFAULT`.
- A feedback run that makes no commit must return a structured `SKIPPED:`
  disposition. Otherwise it fails before force-pushing and cannot be reported
  as a simple rebase.

## Transition (temporary)

The default flipped from "rebase + feedback" to "rebase only" on 2026-07-17. To
avoid a silent surprise, a bare `/rebase` surfaces a temporary notice (chat reply
+ PR comment via `build_alert("NOTE", …)`) pointing users to `/fix` (or
alternatively `/rebase --fix`).
The notice is date-gated by `rebase_transition.FIX_NOTICE_DEADLINE`
(2026-08-17) and disappears automatically; the behavior change is permanent.
After the deadline the notice code + `_FEEDBACK_ON_BY_DEFAULT` are removed
(`apply_feedback = fix`).

## Evaluation

The `rebase` skill is covered by the eval harness (`koan/app/skill_evals.py`;
design in `specs/003-core-skill-evals/`).

- **What's scored:** the already-solved decision JSON
  (`{already_solved, confidence, resolved_by, reasoning}`) from
  `_check_if_already_solved` — JSON validity, decision correctness honoring the
  production rule (`already_solved && confidence == "high"`), confidence
  validity, reasoning presence.
- **Golden dataset:** `koan/skills/core/rebase/evals/cases/*.json` —
  `already_solved` (high-confidence positive), `not_solved` (negative),
  `ambiguous` (precision trap: tangential commit, must not skip).
- **CI:** offline scorer + dataset-validity tests run in the `fast` group and
  never call the Claude subprocess.
- **Live:** `KOAN_EVAL_LIVE=1 python -m app.skill_evals rebase --live` runs the
  real already-solved check over the dataset and compares to
  `evals/baseline.json`.

**Contract:** changing the already-solved decision shape (`rebase_pr.py`) or its
prompt MUST be reflected in the golden cases / baseline.

## Known debt / watch-outs

- Order-sensitive combo `/rr` (review→rebase) must insert both sub-missions in one
  atomic locked write to preserve order and avoid TOCTOU.
