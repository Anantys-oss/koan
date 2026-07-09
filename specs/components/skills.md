---
type: component-spec
title: "Component Spec — Skills System"
description: "Documents the skills system that discovers, routes, and executes `/command` skills (SKILL.md contract, dispatch, the new-skill checklist, and the eval harness)."
tags: [skills]
created: 2026-06-27
updated: 2026-07-09
---

# Component Spec — Skills System

**Modules:** `koan/app/skills.py`, `koan/app/skill_dispatch.py`,
`koan/app/external_skill_dispatch.py`, `koan/skills/core/<name>/`,
`instance/skills/<scope>/<name>/`

> Per-skill specs live in `specs/skills/`. This spec covers the **system** that
> discovers, routes, and executes skills.

## Purpose

An extensible command-plugin system. A "skill" is a `/command` with a `SKILL.md`
(frontmatter contract) and an optional `handler.py`. Skills are how both humans (Telegram,
dashboard) and external systems (GitHub/Jira @mentions) drive Kōan.

## Architecture

```
skills.py            → registry: discover SKILL.md, parse frontmatter (lite YAML),
       │                map commands/aliases → skills, execute_skill()
skill_dispatch.py    → agent-loop direct execution: /command missions bypass the Claude
       │                agent, route to registered runners (plan/rebase/recreate/check/...)
external_skill_dispatch.py → in-process dispatch for custom skills triggered via Jira/GitHub
```

## Skill anatomy

```
koan/skills/core/<name>/
  ├─ SKILL.md      # frontmatter: name, description, group, commands, aliases, flags
  └─ handler.py    # optional: def handle(ctx: SkillContext) -> Optional[str]
```

- **Handler return contract:** string → Telegram reply; `""` → already handled; `None`
  → no message.
- **Prompt-only skills:** omit `handler`, put prompt text after frontmatter → sent to
  Claude directly.

## Frontmatter flags (the contract)

| Flag | Meaning |
|---|---|
| `group:` | **Mandatory.** One of: missions, code, pr, status, config, ideas, system (core); `integrations` reserved for custom skills. Drives `/help`. |
| `worker: true` | Blocking skill (Claude/API) → runs in a background thread. |
| `github_enabled: true` | Triggerable via GitHub @mention (Jira reuses it; no separate `jira_enabled`). |
| `github_context_aware: true` | Accepts extra context after the command. |
| `sub_commands:` | Combo skill — decomposes into multiple sub-missions (discovered by `collect_combo_skills()`). |
| `forward_result: true` (+ `title_markers:`) | Opt-in result forwarding, resolved dynamically — **the pattern for "core recognizes a custom skill" without hardcoding names**. |
| `model_key:` | Selects the model tier (e.g. `mission`). |

## Invariants

- **Names/aliases/dirs use underscores, never hyphens** — Telegram truncates at `-`.
- **No hardcoded skill-name lists in `koan/app/`.** When core must recognize a specific
  custom skill, drive it off SKILL.md frontmatter flags (see `collect_forward_result_markers`).
- **Skill stdout is DATA.** Runners emit structured transcripts; `mission_executor`
  passes `trust_stdout=False` so transcripts aren't misread as CLI errors.
- **No private identifiers leak** into core skills, tests, or docs — use generic
  placeholders (`my_fix`, `my_team`, `PROJ-NNN`).
- **Order-sensitive combos insert atomically** — one locked multi-entry write
  (`insert_pending_missions`), never two top-inserts (TOCTOU + reversed order).

## Adding a core skill (full checklist)

1. `koan/skills/core/<name>/SKILL.md` (+ `handler.py` if needed) with a `group:`.
2. If agent-loop-run: register in `_SKILL_RUNNERS` + `_COMMAND_BUILDERS` +
   `validate_skill_args()` in `skill_dispatch.py`.
3. Add to the CLAUDE.md "Core skills" list (alphabetical).
4. Update `docs/users/user-manual.md` and `docs/users/skills.md`.
5. Add the per-skill spec in `specs/skills/<name>.md`.
6. `TestCoreSkillGroupEnforcement` must pass (fails if `group:` is missing).
7. If the skill is LLM-driven and has a checkable output contract, add eval cases
   (see "Skill evaluation harness" below). Orchestration/queue skills with no
   structured LLM output are exempt — see `EVAL_EXEMPT_SKILLS`.

## Skill evaluation harness

**Module:** `koan/app/skill_evals.py` — a deterministic framework for evaluating
LLM-driven skills against a checked-in golden dataset, so quality regressions
are caught in CI and improvements are measurable across prompt iterations.

**Rule (constitution VII — honest reporting):** a skill gets golden-dataset
evals **iff** it is LLM-driven **and** emits a checkable structured output
(valid JSON / a parseable contract). Skills that lack such a contract are
documented as exempt — fabricating a dataset for them would measure nothing
real. This is enforced at contribute time by the new-skill checklist (step 7)
and pinned by the `TestEvalExemption` guard.

**Covered skills** (scorer + `evals/cases/` + live adapter, all keyed by name in
the `SCORERS` / `LIVE_FNS` registries — adding a skill never edits `run_eval`):

| Skill | Output contract | Scorer reuses |
|---|---|---|
| `review` | JSON findings (`review_schema`) | `validate_review` |
| `fix` | diagnostic `{confidence, hypothesis, code_paths}` | `_parse_diagnostic` shape |
| `plan` | markdown (sections + `#### Phase N:`) | `parse_plan_progress` |
| `brainstorm` | JSON `{issues[]}` w/ 7 `REQUIRED_ISSUE_SECTIONS` | `_parse_decomposition` + `_validate_issue_bodies` |
| `rebase` | JSON `{already_solved, confidence}` decision | `_check_if_already_solved` rule |

**Exempt skills** (`EVAL_EXEMPT_SKILLS`, pinned by a guard test — quality bar is
behavioural unit tests instead):

| Skill | Why exempt |
|---|---|
| `implement` | orchestration: `run_implement()` returns `(success, summary)`, mutates files + opens a PR — no structured artifact to score |
| `mission` | pure-Python queue utility — no LLM at all |

- **Per-skill data** lives with the skill: `koan/skills/core/<name>/evals/cases/*.json`
  (golden inputs + expectations) + `evals/baseline.json` (last-known-good live
  scores). `EvalCase.diff` is the `review` input; other skills carry inputs in
  `EvalCase.input` (e.g. `issue_*`, `idea`, `topic`, `pr_*`).
- **Scorer dispatch** is keyed by skill name via the `SCORERS` registry
  (`register_scorer`/`get_scorer`); the CLI resolves the live adapter per skill
  via `LIVE_FNS` (`get_live_fn`), reporting "no live adapter" honestly when absent.
- **Two modes:** offline (default, CI-safe — scores canned outputs, never calls
  the Claude subprocess) and live (opt-in via `KOAN_EVAL_LIVE`, composes the
  skill's real pipeline seams, compares to `baseline.json`, exits non-zero on
  regression).
- **Single source of truth:** each scorer reuses that skill's own existing
  validator/parser rather than re-implementing the contract.

**Design contract:** `specs/002-review-skill-evals/` (review),
`specs/003-core-skill-evals/` (multi-skill). **Operator runbook:**
`docs/operations/skill-evals.md`.

## Integration points

- Bridge dispatch (`command_handlers.py`) and agent-loop dispatch (`mission_executor`)
  both call into `skills.py`.
- Custom skills under `instance/skills/<scope>/` can be cloned Git repos for team sharing
  (`skill_manager.py`).
- GitHub/Jira @mentions route through `external_skill_dispatch.py`.

### `review` diff-size & partial-coverage contract

- **Single source of truth for diff size** is the compressor token budget
  (`optimizations.review_compressor.token_budget`, default 80,000), read via
  `config.get_review_compressor_token_budget()`. The fetch-time character cap is
  *derived* from it — `config.get_review_max_diff_chars()` = budget × 3.5 × 4 —
  so there is no independent, conflicting numeric cap.
- `fetch_pr_context(...)` (in `rebase_pr.py`) takes `max_diff_chars` (legacy default
  32 000 for rebase/squash/recreate/ci_queue callers; `/review` passes the derived
  cap) and returns a `diff_skipped_files` list via `utils.truncate_diff_with_skips()`
  so files cut at fetch time are first-class, not buried in the diff footer.
- `review_runner.build_review_prompt()` returns a `(prompt, coverage_note)` tuple.
  `_build_coverage_note()` merges fetch-time skips, compressor skips, and triaged
  files into **one** value used both for the `{SKIPPED_FILES}` prompt slot and the
  returned note — the two can never diverge. `_post_review_comment(..., coverage_note=)`
  prepends the note (a `⚠️ Partial review` block) above the review body, before the
  60 K GitHub-length truncation, so partial coverage is never silent.

## Known debt / watch-outs

- Frontmatter is parsed by a custom lite YAML parser (no PyYAML) — keep frontmatter
  simple; exotic YAML will not parse.
- ~80 of ~91 skills lack per-skill specs (phase 1 ships 10 exemplars).

## Change protocol

Changing the SKILL.md contract, dispatch routing, or the group enumeration updates this
spec, the README authoring guide (`koan/skills/README.md`), and the group-enforcement
test.
