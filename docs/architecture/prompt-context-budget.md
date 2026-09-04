---
type: doc
title: "Prompt Context Budget"
description: "Measures what every mission prompt costs — the fixed agent.md base, the conditional sections, per-skill prompt sizes — and separates what an instance operator can tune from what is frozen in the repo."
tags: [architecture, prompts, context]
created: 2026-07-22
updated: 2026-07-22
---

# Prompt Context Budget

Every mission Kōan runs pays a fixed context cost before the mission
instruction is even read. This page measures that cost, breaks it into the
non-negotiable base, the conditional sections, and the per-skill prompt, and
states which parts an instance operator can tune.

Assembly lives in `koan/app/prompt_builder.py` (`build_agent_prompt` and
`build_agent_prompt_parts`); see `specs/components/agent-loop.md` for the
contract.

## The non-negotiable base

Loaded for every mission, regardless of skill or project:

| Element | Chars | ~Tokens |
| --- | --- | --- |
| `koan/system-prompts/agent.md` | 21 621 | ~5 400 |
| Resolved partials (`cli-execution-model`, `temp-hygiene`, `test-guidance`) | 2 544 | ~640 |
| **Base total** | **~24 200** | **~6 000** |

Partials are pulled in by `{@include <name>}` directives resolved in
`app/prompts.py::_resolve_includes`, so their weight is part of the base even
though it is not visible in `agent.md`'s own byte count.

`soul.md` is **not** inlined. `agent.md` opens with an instruction to read
`{INSTANCE}/soul.md`, so its content arrives as a tool-call result in the
conversation rather than in the prefix-cached system prompt. Size therefore
varies with the file (the shipped `instance.example/soul.md` is ~13 700 chars)
and it is paid once per mission, not per turn.

## Conditional sections

Appended by `build_agent_prompt` depending on mission type, autonomous mode,
project layout, and `config.yaml` toggles. In the worst case these outweigh the
base:

| Section | Chars | Trigger |
| --- | --- | --- |
| `testing-anti-patterns.md` | 8 784 | Test/implementation-shaped missions |
| `KOAN.md` + `.koan/KOAN.md` | up to 16 000 (capped) | Present in the target repo |
| `security-flagging.md` | 2 670 | Autonomous mode |
| `submit-pull-request.md` | 1 892 | Nearly always |
| `verification-gate.md` | 1 883 | Mission-title dependent |
| `tdd-mode.md` | 1 772 | Mission-title dependent |
| Learnings + memory log | Variable | Bounded by the `memory:` recall config |
| caveman / ponytail / rtk / focus / verbose / language / merge-policy | Hundreds each | `config.yaml` toggles |

The `KOAN.md` cap is `_MAX_KOAN_MD_CHARS = 16000` in `app/project_koan.py`, and
applies to the root and `.koan/` files combined. Memory recall defaults to 40
relevant learnings plus a 5-entry recency hedge on the agent-loop path
(`_load_recall_config`), looser than the skill-side defaults.

## Per-skill prompt sizes

Only the prompt for the current step is loaded — not the whole `prompts/`
directory. Multi-step skills spend their remaining prompts on later turns.

| Skill | Entry prompt | Chars | Additional prompts (separate turns) |
| --- | --- | --- | --- |
| `review` | `review.md` | 4 905 | architecture, comments, with-plan, reflect, triage, silent-failure-hunter — 24 326 total |
| `plan` | `plan.md` | 4 537 | assumptions, critic, improve, iterate, review — 18 589 total |
| `implement` | `implement.md` | 4 431 | retry context (2 662), PR summary (425) |
| `rebase` | `rebase.md` | 2 533 | conflict resolution, CI fix, already-solved — 8 282 total |
| `fix` | `fix.md` | 1 852 | `fix-diagnose.md` (1 645), separate invocation |

## Practical total

A realistic mission costs roughly:

- ~6 000 tokens of base, plus
- ~2 000–7 000 tokens of conditional sections, plus
- ~500–1 200 tokens of skill prompt

for **~9 000–14 000 tokens** before the target repo's `CLAUDE.md` (loaded by the
CLI itself, not by Kōan) and the CLI provider's own system prompt.

## What an operator can tune

These live in the instance tree or the target repo and need no change to Kōan:

| File | Effect on the budget |
| --- | --- |
| `instance/soul.md` | Identity/tone; read at runtime, so its size is a per-mission cost |
| `instance/config.yaml` | Toggles caveman, ponytail, rtk, focus, verbose, language, merge policy; sets the `memory:` recall caps |
| `instance/memory/**` | Learnings and memory-log entries injected into the prompt, bounded by the recall caps |
| `instance/skills/<scope>/<name>/` | Private skills and their own prompts |
| `<project>/KOAN.md`, `<project>/.koan/KOAN.md` | Kōan-only project guidance, capped at 16 000 chars |
| `<project>/.koan/skills/<skill>/*.md` | Append-only steering added to a built-in skill prompt |
| `<project>/CLAUDE.md` | Loaded by the CLI itself, outside Kōan's assembly |

## What is frozen

These are repo artifacts and require a pull request to change:

- `koan/system-prompts/agent.md` and `koan/system-prompts/_partials/*`
- Every conditional section: `testing-anti-patterns.md`, `tdd-mode.md`,
  `verification-gate.md`, `submit-pull-request.md`, `security-flagging.md`
- `koan/skills/core/*/prompts/*.md`

## Where the leverage is

The ~24 000 chars of base plus the 8 784 chars of testing anti-patterns are the
bulk of the fixed cost and are frozen. The only operator-side levers that move
the number meaningfully are the `memory:` recall caps and the size of the
project's `KOAN.md`.

## Related

- `docs/design/memory-injection.md` — how learnings and memory-log entries are
  selected for injection
- `docs/users/koan-md.md` — the `KOAN.md` convention
- `specs/components/agent-loop.md` — the agent-loop contract
