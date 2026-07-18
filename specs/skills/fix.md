---
type: skill-spec
title: "Skill Spec — fix"
description: "Specifies the `/fix` skill, which fixes a tracker issue end-to-end (or batch-queues fixes for a repo) and redirects PR URLs to `/rebase --fix`, with eval coverage on its diagnostic output."
tags: [skill]
created: 2026-06-27
updated: 2026-07-17
---

# Skill Spec — `fix`

## Command(s)

- **Primary:** `/fix [--now] <issue-url> [context]` · `/fix <repo-url> [--limit=N]`
- **Group:** `code`

## Purpose

Fix a tracker issue end-to-end (understand → plan → test → implement → draft PR), or
batch-queue fix missions for all open issues in a repo.

See `docs/users/skills.md` for the end-user `/fix` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| issue URL | command arg | yes (or repo URL) | GitHub or Jira |
| repo URL + `--limit=N` | command arg | alt form | batch all open issues |
| `--now` | flag | no | queue at top |
| trailing context | command arg | no | extra guidance |

## Outputs / side effects

- Queues fix mission(s) (`model_key: mission`); agent opens a draft PR per issue.
- On a PR URL, `/fix` **redirects to `/rebase --fix`** (same intent: address PR
  concerns) — it injects `--fix` because a bare `/rebase` now only rebases —
  preserving `--now` + trailing context.

## Error cases

| Condition | Behavior |
|---|---|
| invalid URL | reply with usage |
| PR URL given | delegated to `rebase/handler.py` with `--fix` injected into `ctx.args` |
| batch with no open issues | nothing queued, informative reply |

## Integration hooks

- **Handler:** `handler.py` (delegates to `rebase/handler.py` for PR URLs).
- **GitHub/Jira:** `github_enabled` + `github_context_aware`.

## Invariants

- PR-URL redirect keeps `ctx` intact (so `--now` and post-URL context survive) and
  injects `--fix` so the delegated `/rebase` addresses review feedback.
- Always draft PR on `<prefix>/*`.

## Evaluation

The `fix` skill is covered by the eval harness (`koan/app/skill_evals.py`;
design in `specs/003-core-skill-evals/`).

- **What's scored:** the pre-fix diagnostic's structured output
  (`fix_diagnose._parse_diagnostic` → `{confidence, hypothesis, code_paths}`):
  confidence validity + match, hypothesis presence + keyword recall, code-path
  keyword recall.
- **Golden dataset:** `koan/skills/core/fix/evals/cases/*.json` — `null_pointer`
  (HIGH), `race_condition` (MEDIUM), `vague_report` (LOW precision trap).
- **CI:** offline scorer + dataset-validity tests run in the `fast` group and
  never call the Claude subprocess.
- **Live:** `KOAN_EVAL_LIVE=1 python -m app.skill_evals fix --live` runs the real
  diagnostic over the dataset and compares to `evals/baseline.json`.

**Contract:** changing the diagnostic output shape (`fix_diagnose.py`) or its
prompt MUST be reflected in the golden cases / baseline.

## Known debt / watch-outs

- The issue-vs-PR branch is URL-shape-driven; `github_url_parser` is the single
  classifier — don't reimplement URL detection in the handler.
