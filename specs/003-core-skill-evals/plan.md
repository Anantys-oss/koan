# Implementation Plan: Core Skill Evaluation Harness (multi-skill)

**Branch**: `003-core-skill-evals` | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-core-skill-evals/spec.md`

## Summary

Extend the review eval harness (`koan/app/skill_evals.py`, shipped by #2265) to
the four core skills that are LLM-driven **and** emit a checkable structured
output — `fix`, `plan`, `brainstorm`, `rebase`. Approach: generalise the
review-shaped data model (optional `diff`, generic `input`, `CaseExpect.raw`),
add four deterministic scorers that reuse each skill's existing validator/parser
as the single source of truth, ship a golden dataset per skill, and extend the
offline pytest suite so regressions fail the `fast` CI group. The two remaining
named skills — `implement` (orchestration, no structured parse) and `mission`
(pure-Python queue, no LLM) — are documented as eval-exempt with rationale and
pinned by a guard test, rather than receiving fabricated datasets. The
eval-required rule is made discoverable in the skill-authoring CLAUDE.md.

## Technical Context

**Language/Version**: Python 3.11+ (no syntax/stdlib after 3.11 — constitution
constraint). No new third-party deps.

**Primary Dependencies**: Existing stack only. Reuses:
- `app.review_schema.validate_review` (review validity — unchanged).
- `app.dashboard_service.plans.parse_plan_progress` (plan phase parsing — single
  source of truth for `#### Phase N:` detection).
- `skills.core.brainstorm.brainstorm_runner.REQUIRED_ISSUE_SECTIONS` +
  `_validate_issue_bodies` + `_parse_decomposition` (brainstorm contract).
- `skills.core.fix.fix_diagnose._parse_diagnostic` + `_CONFIDENCE_RE`/`_SECTION_RE`
  semantics (fix diagnostic contract).
- `app.rebase_pr._check_if_already_solved` JSON shape
  `{already_solved, confidence, resolved_by, reasoning}` (rebase decision).

**Storage**: None new. Golden datasets are checked-in JSON under each skill's
`evals/cases/`; baselines under `evals/baseline.json`. Runtime state unchanged.

**Testing**: `pytest` with `KOAN_ROOT=/tmp/test-koan` prefix. Never call the
Claude subprocess; mock provider seams. New/extended:
`koan/tests/test_skill_evals.py`. Offline by default; one opt-in
`@pytest.mark.slow` live test per skill (skips without `KOAN_EVAL_LIVE`).

**Target Platform**: Same as Kōan (macOS/Linux daemon host). The harness is a
library + CLI; live evals run in an operator's shell.

**Project Type**: Library extension — generalise one existing module + four
per-skill scorers + golden data + tests + docs/specs. Not a service.

**Performance Goals**: None. The relevant bound is determinism: scorers are
pure, offline, and fast (sub-millisecond per case).

**Constraints**:
- Constitution VII (Honest Reporting): do not fabricate evals for non-evaluable
  skills; document `implement`/`mission` as exempt with rationale.
- Constitution VII (Simplicity): reuse existing validators as single source of
  truth; no parallel schema.
- Constitution: never call the Claude subprocess in tests (live is opt-in).
- No private operator identifiers in public artifacts.

**Scale/Scope**: 1 module generalised (`skill_evals.py`), 4 scorers + 4 live
adapters, ~3–5 golden cases × 4 skills, ~1 guard test, extended
`test_skill_evals.py`, 4 per-skill spec Evaluation sections (1 new: brainstorm),
`specs/components/skills.md` + `koan/skills/CLAUDE.md` + runbook updates. Out of
scope: live adapters for implement/mission (no contract), refactoring the four
skills' pipelines (the harness composes existing seams only).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against `.specify/memory/constitution.md`.

| # | Principle | Verdict | How this design honours it |
|---|---|---|---|
| I | Human Authority | ✅ PASS | No shipping action; evals are a quality tool. Draft PR only, prefixed branch. |
| II | Specs Are Source of Truth | ✅ PASS | This trio (`spec/plan/tasks`) + updates to `specs/components/skills.md` and per-skill specs. |
| III | Local Files, Atomic State | ✅ PASS | No new runtime state files; only checked-in golden data + baseline stubs. |
| IV | Provider Isolation | ✅ PASS | Scorers are provider-agnostic; live adapters compose each skill's existing seam. |
| V | Untrusted Inputs, Audited Outputs | ✅ PASS | Golden case content is hand-authored and generic (no private identifiers); scorers treat LLM output as untrusted data and never raise. |
| VI | Single Writer, Single Read Path | ✅ PASS | Each scorer reuses exactly one existing validator/parser (no parallel schema); `run_eval` keeps one dispatch path. |
| VII | Simplicity and Honest Reporting | ✅ PASS | **The core decision:** do not fabricate evals for `implement`/`mission`. Generalise the model minimally; document the exemption. |

## Project Structure

### Documentation (this feature)

```text
specs/003-core-skill-evals/
├── spec.md              # /speckit-specify output (this feature's contract)
├── plan.md              # This file
└── tasks.md             # /speckit-tasks output
```

### Source Code (repository root)

```text
koan/
├── app/
│   └── skill_evals.py            # GENERALISED: optional diff, input dict, CaseExpect.raw,
│                                 #   ScorerFn(object), 4 new scorers, LIVE_FNS registry,
│                                 #   CLI dispatch by skill, EVAL_EXEMPT_SKILLS
├── skills/core/
│   ├── fix/evals/                # NEW: cases/*.json + baseline.json
│   ├── plan/evals/               # NEW: cases/*.json + baseline.json
│   ├── brainstorm/evals/         # NEW: cases/*.json + baseline.json
│   └── rebase/evals/             # NEW: cases/*.json + baseline.json
└── tests/
    └── test_skill_evals.py       # EXTENDED: 4 scorers' pass/fail + dataset validity +
                                  #   injected-seam live-adapter tests + exemption guard

specs/
├── components/skills.md          # UPDATE: list evaluated + exempt skills, rule
└── skills/
    ├── fix.md                    # UPDATE: add Evaluation section
    ├── plan.md                   # UPDATE: add Evaluation section
    ├── brainstorm.md             # NEW: per-skill spec + Evaluation section
    └── rebase.md                 # UPDATE: add Evaluation section

koan/skills/CLAUDE.md             # UPDATE: new-skill checklist eval obligation
docs/operations/skill-evals.md    # UPDATE: add the 4 skills + exemption guidance
```

**Structure Decision**: Single-project layout (the repo's existing shape). No new
packages; the feature extends one app module and adds data + tests in place.

## Complexity Tracking

> The generalisation adds one optional field + one dict to `EvalCase`, one `.raw`
> to `CaseExpect`, and loosens `ScorerFn`'s second param to `object`. This is the
> minimal change that lets non-review cases load without a dataclass-per-skill
> explosion (which would violate Principle VII's simplicity clause). No violations
> to justify.
