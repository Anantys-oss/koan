---
type: skill-spec
title: "Skill Spec — commit"
description: "Specifies the `/commit` skill, which analyzes staged/unstaged git diffs and creates one local conventional commit without pushing, refusing protected base branches and secret files."
tags: [skill]
created: 2026-07-21
updated: 2026-07-21
---

# Skill Spec — `commit`

## Command(s)

- **Primary:** `/commit [project] [message hint]`
- **Aliases:** `/cm`
- **Group:** `code`

## Purpose

Analyze the project working tree (staged and unstaged), craft a conventional
commit message, stage a coherent set of files, and create **one** local commit.
Ships as a core skill so every instance has a consistent commit workflow without
relying on an external user skill.

See `docs/users/skills.md` and `docs/users/user-manual.md` for the end-user
reference.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| project name | command arg (first token) | no | resolved via `projects.yaml`; unknown token becomes hint on default project |
| message hint | remaining args / context file | no | free-text subject guidance for the model |

## Outputs / side effects

- **Bridge handler:** validates git state (repo, conflicts, non-empty tree), then
  queues a Pending mission: `- [project:<name>] /commit [hint]`.
- **Agent-loop runner:** preflight re-checks branch/conflicts/status, invokes the
  CLI provider with `prompts/commit.md`, stages/commits via Bash tools.
- **Telegram:** start notify + outcome (or error) via `notify_fn` /
  `notify_outcome`.
- **No push**, no PR, no amend of prior commits.

## Error cases

| Condition | Behavior |
|---|---|
| no projects configured / unknown project with no default | reply with ❌; nothing queued |
| not a git repo | ❌ at handler (and runner preflight) |
| HEAD on `main`/`master` | runner aborts (handler does not block on branch; runner refuses) |
| unresolved merge conflicts | ❌ abort at handler and runner |
| conflict probe / status git command fails | ❌ hard abort (indeterminate state is not treated as clean) |
| working tree clean | ❌ nothing to commit |
| model output claims success but HEAD unchanged | runner returns failure (exit 1); success requires HEAD SHA advance |
| unrecognized / truncated model output | failure by default |
| unreadable `--context-file` | warning on stderr; run continues with empty hint |

## Integration hooks

- **Handler:** `koan/skills/core/commit/handler.py` (audience: hybrid).
- **Runner:** `skills.core.commit.commit_runner` registered in
  `skill_dispatch._SKILL_RUNNERS` as `commit` (alias `cm`).
- **GitHub/Jira:** not enabled (`github_enabled` absent) — local git only.
- **Combo / worker:** no.
- **Auto-merge / security review:** N/A (no PR produced).

## Invariants

- **Success iff HEAD advances.** Model tokens (`COMMITTED` / `ABORTED`) inform
  the human summary; they never alone set the runner's success bit or exit code.
- **One local commit only** per invocation; never push; never amend unless the
  human explicitly asked (the default prompt forbids amend).
- **Never commit to `main`/`master`.** Prefer loud failure over base-branch
  commits.
- **Never stage secrets** (`.env`, keys, credentials) — enforced in the prompt
  contract; preflight does not scan content but refuses empty trees after filter
  via the model abort path.
- Alias is **`/cm`**, not `/ci`, so continuous-integration commands stay
  unambiguous.

## Evaluation

`commit` is **eval-exempt** as a hybrid queue + git orchestration skill: the
handler is pure Python queueing, and the runner's correctness is behavioral
(preflight + HEAD-change confirmation), covered by
`koan/tests/test_commit_skill.py` rather than the LLM skill-eval harness.

## Known debt / watch-outs

- Handler preflight does **not** refuse `main`/`master`; the runner does. A
  mission can still be queued while on a protected branch and then abort at
  run time — intentional so the human sees the refusal when the agent would
  act, but a future tighten could mirror the branch check in the handler.
- Staging/secret filtering is prompt-enforced, not a hard Python gate on
  `git add` paths.
