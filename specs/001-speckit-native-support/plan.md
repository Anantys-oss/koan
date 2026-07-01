# Implementation Plan: Native Spec-Kit (`/speckit`) Mission Orchestration

**Branch**: `001-speckit-native-support` | **Date**: 2026-06-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-speckit-native-support/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Add native [spec-kit](https://github.com/github/spec-kit) support to Kōan as a **mission-queuing skill** modeled on `/implement`. An operator triggers `/speckit` from chat, an issue URL, an `@bot` mention, or `/speckit_from_branch <repo-id> <branch>` (resume from a human-validated spec). A `handler.py` runs **code-enforced pre-gates** (constitution presence + quota affordability), then queues a **single** Claude mission whose prompt orchestrates the speckit pipeline — specify → plan → tasks → implement (skipping `specify` for the from-branch variant) — followed by a best-effort private review loop, CI validation, per-task commits, and a draft PR. The speckit sub-skills (`speckit-specify`, `speckit-plan`, `speckit-tasks`, `speckit-implement`) are Claude Code skills the agent invokes mid-mission. Hard-abort on steps 1–4 and best-effort steps 5–6 are encoded in the orchestration prompt; the load-bearing safety gates (constitution, quota, draft-PR-only, prefixed-branch) are code-enforced per the constitution.

## Technical Context

**Language/Version**: Python 3.11+ (no syntax/stdlib after 3.11 — constitution constraint).

**Primary Dependencies**: Existing Kōan stack only — no new third-party deps. Reuses: `app.skills` (SkillRegistry, SkillContext), `app.utils.insert_pending_mission` / `append_to_outbox`, `app.github_skill_helpers` (project resolution, mission queuing), `app.config` (accessor pattern), `app.usage_tracker` (`remaining_budget`), `app.github.pr_create` (draft), `app.claude_step.run_ci_fix_loop`, `app.ci_queue_runner.check_ci_status`, `app.missions` (`requeue_mission`, lifecycle), `app.prompts.load_skill_prompt`. The speckit sub-skills are provided by the spec-kit integration already scaffolded under `.specify/` (Claude Code skills, not Python deps).

**Storage**: None new. Runtime state stays in existing `instance/` files (`missions.md`, `outbox.md`, `config.yaml`). Speckit design artifacts (`spec.md`/`plan.md`/`tasks.md`) live in the **target project's** tree, not Kōan's.

**Testing**: `pytest` with `KOAN_ROOT=/tmp/test-koan` prefix. Mock `format_and_send` (never call the Claude subprocess). New: `koan/tests/test_speckit_skill.py` + extend `TestCoreSkillGroupEnforcement` coverage. Test behavior (gates fire, mission queued/held, prompt assembled), never source text.

**Target Platform**: Same as Kōan (macOS/Linux daemon host running the Claude Code CLI). The feature targets the agent-loop host; the speckit pipeline runs inside a Claude Code session.

**Project Type**: Library/daemon extension — two new core skills (`speckit`, `speckit_from_branch`) + one shared orchestration helper module under `koan/app/`. Not a web service, not a standalone CLI.

**Performance Goals**: No throughput targets. The relevant bound is **quota economics**: a `/speckit` run is a long, token-heavy multi-step mission. The 15% affordability start-gate (FR-017) prevents starting runs that cannot finish. Per-task commits (FR-019) bound rework loss.

**Constraints**:
- Constitution Principle I: draft PRs only, prefixed (`koan/*`) branches, never merge, never touch the default branch.
- Constitution Principle V: inbound content is untrusted data (prompt guard); the **load-bearing** gates (constitution, quota, draft-PR, branch) MUST be code-enforced, not merely prompt-advised.
- Constitution Principle VI: `missions.md` mutations only via lifecycle functions; one config read path per concern (`get_speckit_config()`).
- Constitution Principle VII: extend existing mechanisms; no parallel mission-queuing or PR-creation paths.
- No private operator identifiers leak into public artifacts.

**Scale/Scope**: 2 new core skills + 1 shared module (`~3` new Python files + 1–2 prompt files), 1 config accessor, `TestCoreSkillGroupEnforcement` must pass, user-manual + skills-reference updates, a per-skill design spec at `specs/skills/speckit.md`. Out of scope: auto-initializing spec-kit for a project, multi-PR-per-run, auto-merge.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against `.specify/memory/constitution.md` v1.0.0. All seven principles satisfied; the design explicitly defers to them.

| # | Principle | Verdict | How this design honors it |
|---|---|---|---|
| I | Human Authority (NON-NEGOTIABLE) | ✅ PASS | Draft PRs only via `pr_create(draft=True)`; prefixed `koan/*` branches; never merge; never commit to default branch. All shipping is human-gated. |
| II | Specs Are the Source of Truth | ✅ PASS | A per-skill design spec `specs/skills/speckit.md` is produced alongside the code (this feature's trio lives at `specs/001-speckit-native-support/`; see Project Structure + the `SPECS_DIR_COLLISION` note). |
| III | Local Files, Atomic State | ✅ PASS | No new state files; mission/outbox mutations go through existing `insert_pending_mission` / `append_to_outbox` (atomic, locked). |
| IV | Provider Isolation | ✅ PASS | `/speckit` is provider-agnostic — it queues a mission the agent loop hosts; no branching on which CLI is in use. |
| V | Untrusted Inputs, Audited Outputs | ✅ PASS | Inbound mission/issue/comment text passes the existing prompt guard; outbound PR/commit content passes the outbound scanner. **Load-bearing gates (constitution, quota, draft-PR, branch) are code-enforced**, documented as such; prompt-level abort/commit guidance is advisory. |
| VI | Single Writer, Single Read Path | ✅ PASS | `missions.md` mutated only via lifecycle functions; speckit config read through one accessor `get_speckit_config()`; project resolution reuses `resolve_project_path`. |
| VII | Simplicity & Honest Reporting | ✅ PASS | Extends `/implement`'s mission-queuing pattern; reuses CI-fix loop, PR creation, outbox, prompt loader — no parallel paths. Honest: the prompt-driven abort/commit cadence is advisory and stated as such. |

**Gate result**: PASS — no violations. Complexity Tracking table left empty (no justified violations).

## Project Structure

### Documentation (this feature)

```text
specs/001-speckit-native-support/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── skill-interface.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
koan/
├── app/
│   └── speckit_orchestration.py   # NEW: shared helper — constitution gate, quota gate,
│                                  #       project resolution, prompt assembly, mission queuing.
│                                  #       Called by both skill handlers (DRY; centralizes
│                                  #       code-enforced gates). NOT a hardcoded skill-name list.
├── skills/core/
│   ├── speckit/                   # NEW core skill: /speckit
│   │   ├── SKILL.md               # group: code; github_enabled; github_context_aware; model_key: mission
│   │   ├── handler.py             # thin: parse args → speckit_orchestration.dispatch(entry_mode="agent")
│   │   └── prompts/
│   │       └── speckit.md         # orchestration prompt (loaded via load_skill_prompt; no inline prompts)
│   └── speckit_from_branch/       # NEW core skill: /speckit_from_branch
│       ├── SKILL.md               # group: code; github_enabled; github_context_aware; model_key: mission
│       ├── handler.py             # parse repo-id + branch → speckit_orchestration.dispatch(entry_mode="from_branch")
│       └── prompts/
│           └── speckit.md         # from-branch variant prompt (specify-skip, base=human branch)
└── tests/
    └── test_speckit_skill.py      # NEW: gate behavior, mission queuing/hold, prompt assembly, arg parsing
```

Plus edits to existing files (no new modules):
- `koan/app/config.py` — add `get_speckit_config()` accessor (follows `get_review_reflect_config()`).
- `koan/app/mission_executor.py` (or `iteration_manager.py`) — speckit-specific **quota start-gate**: when picking a `/speckit` mission, if `remaining_budget() < threshold`, leave it Pending (hold) and skip; proceeds automatically when quota recovers.
- `CLAUDE.md` — add `speckit`, `speckit_from_branch` to the "Core skills" list (alphabetical).
- `docs/users/user-manual.md` + `docs/users/skills.md` — document both commands.
- `specs/skills/speckit.md` — durable per-skill design contract (Principle II).

**Structure Decision**: Single-project extension (Option 1). Two thin skill handlers delegate to one shared `koan/app/speckit_orchestration.py` module so the code-enforced gates and prompt assembly live in exactly one place (Principle VI — single authority). The speckit sub-pipeline is driven by a prompt file per skill (`prompts/speckit.md`), keeping Python free of inline LLM prompts (constitution constraint).

> **Plan correction (2026-06-29, implement phase).** Tracing `mission_executor._handle_skill_dispatch` → `skill_dispatch.is_skill_mission` / `dispatch_skill_mission` / `build_skill_command` showed a queued `/speckit` mission is dispatched like `/implement`: through a **runner module** (`_SKILL_RUNNERS`, e.g. `skills.core.speckit.speckit_runner`) plus a **command-builder** (`_COMMAND_BUILDERS`, e.g. `_build_speckit_cmd`), with arg validation in `validate_skill_args`. The earlier "zero `skill_dispatch.py` changes" assumption (research R1) was **wrong**. Corrected integration:
> - `koan/skills/core/speckit/speckit_runner.py` + `koan/skills/core/speckit_from_branch/speckit_from_branch_runner.py` — runner modules (modeled on `implement_runner`) that build the Claude command carrying the orchestration prompt.
> - `koan/app/skill_dispatch.py` — register both in `_SKILL_RUNNERS`; add `_build_speckit_cmd` (and the from-branch variant) to `_COMMAND_BUILDERS`; add speckit to `validate_skill_args`.
> - Do **not** create a stub `*_runner.py` before it is functional — `_discover_runner_module` auto-discovers `<name>_runner.py`, which would route `/speckit` missions to a no-op stub. Runner + registration land together in the Foundational phase.
> - The handler (`handler.py`) still queues `- [project:name] /speckit <goal>` at the bridge; the runner executes it in the agent loop. `speckit_orchestration.py` holds the shared gates/prompt-assembly used by both.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

*(Empty — no violations. No simpler alternative was rejected: the design reuses `/implement`'s queuing pattern, the existing CI-fix loop, and `pr_create`. The one genuinely new concept — the per-target-project constitution gate — is the feature's core purpose and cannot be simplified away.)*

## Deferred to `/speckit-tasks`

- Exact hook point for the quota start-gate (mission_executor dispatch vs iteration_manager) — both viable; tasks.md picks one.
- Whether `speckit_from_branch` is a sibling skill or an alias of `speckit` — plan chooses **sibling skill sharing one orchestration module**; tasks.md finalizes the SKILL.md wiring.
- Repo-id accepted forms (project name vs `owner/repo` vs URL) — handled by the existing `resolve_project_path` precedence; tasks.md adds tests for each.
- Post-mission artifact verification (best-effort code check that `spec.md`/`plan.md`/`tasks.md` exist) — optional hardening in tasks.md.
