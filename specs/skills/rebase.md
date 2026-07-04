---
type: skill-spec
title: "Skill Spec — rebase"
tags: [skill]
created: 2026-06-27
updated: 2026-07-02
---

# Skill Spec — `rebase`

## Command(s)

- **Primary:** `/rebase [--now] <pr-url> [context]`
- **Aliases:** `rb`
- **Group:** `pr`

## Purpose

Rebase a PR onto current base and address review concerns — the standing workflow for
keeping a Kōan PR current and merge-ready. `/fix` on a PR URL redirects here.

See `docs/users/skills.md` for the end-user `/rebase` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR URL | command arg | yes | parsed by `github_url_parser` |
| `--now` | flag | no | queue at top |
| trailing context | command arg | no | threaded into the queued mission |

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

- **Handler:** `handler.py` (also the redirect target of `fix`).
- **GitHub:** `github_enabled` + `github_context_aware`.
- **Combo:** second leg of `review_rebase` (`/rr`).

## Invariants

- Post-URL context must thread into the queued mission.
- Multi-account pushes resolve the remote owner's token; tokens redacted in logs.

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
