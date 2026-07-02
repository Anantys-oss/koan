# Implementation Plan: Review Skill Evaluation Harness

**Branch**: `002-review-skill-evals` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-review-skill-evals/spec.md`

## Summary

Add a deterministic **skill-evaluation harness** (`koan/app/skill_evals.py`) that
scores a review skill's JSON output against a checked-in **golden dataset**
(`koan/skills/core/review/evals/cases/*.json`). It runs two ways: **offline**
(default — scorer + dataset validity + recorded outputs, no LLM, runs in `fast`
CI) and **live** (opt-in via `KOAN_EVAL_LIVE`; invokes the real review pipeline
through existing seams, scores each case, compares to a `baseline.json`). A
skill-keyed scorer registry makes extending to other skills a matter of adding a
scorer + a `cases/` dir. This lets maintainers detect review-quality regressions
in CI and confirm improvements across prompt iterations.

## Technical Context

**Language/Version**: Python 3.11+ (no 3.12/3.13 syntax).

**Primary Dependencies**: stdlib only (`dataclasses`, `json`, `pathlib`,
`difflib` n/a). Reuses `app.review_schema.validate_review`,
`app.review_runner.{build_review_prompt,_run_claude_review,_parse_review_json}`.
No new third-party deps.

**Storage**: JSON case files + `baseline.json` under the skill dir (version
controlled). No `instance/` writes, no atomic-write concern (these are read-only
data files, not shared runtime state).

**Testing**: pytest, `KOAN_ROOT` set, `from app...` imports. New
`koan/tests/test_skill_evals.py` (offline, `fast` group) + one `@pytest.mark.slow`
live test that skips without `KOAN_EVAL_LIVE`.

**Project Type**: library/daemon module + CLI entry (`python -m app.skill_evals`).

**Constraints**: ≥ 90% coverage on the new module (repo baseline 88%, 0.5% gate);
ruff `PERF`/`SIM105`/`F541` clean; never invoke Claude in offline tests.

**Scale/Scope**: one module (~250–350 LOC), ~5 golden cases, one test file.

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Human Authority | ✅ pass | Read-only eval; writes only a checked-in data baseline on explicit `--update-baseline`. No git/merge/deploy. |
| II. Specs Are Source of Truth | ✅ pass | This plan + spec; updates `specs/skills/review.md` and `specs/components/skills.md` in-branch. |
| III. Local Files, Atomic State | ✅ N/A | No `instance/` writes; cases/baseline are version-controlled repo files. |
| IV. Provider Isolation | ✅ pass | Live mode crosses the provider seam only via `_run_claude_review`; never branches on provider name. |
| V. Untrusted Inputs, Audited Outputs | ✅ pass | Cases are operator-authored repo data (trusted). CLI output is local-only; no outbox/PR/commit emission. |
| VI. Single Writer, Single Read Path | ✅ pass | One scorer per skill via registry; one case loader; one `validate_review` source of truth. |
| VII. Simplicity & Honest Reporting | ✅ pass | Extends existing seams; YAGNI list in research.md §8; scorer is pure/deterministic. |

No violations — Complexity Tracking table intentionally empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-review-skill-evals/
├── spec.md              # feature spec (specify step)
├── research.md          # phase 0 (plan step)
├── plan.md              # this file (plan step)
└── tasks.md             # phase 2 (tasks step)
```

### Source Code (repository root)

```text
koan/
├── app/
│   └── skill_evals.py            # NEW: EvalCase/CaseExpect/FindingExpect dataclasses,
│                                 #      score_review(), run_eval(), load_cases(),
│                                 #      SCORERS registry, CLI main(), live adapter.
├── skills/core/review/
│   └── evals/                    # NEW: review-skill eval data
│       ├── cases/                # NEW: golden cases
│       │   ├── sql_injection.json
│       │   ├── bare_except.json
│       │   ├── hardcoded_secret.json
│       │   ├── clean_refactor.json
│       │   └── benign_style.json     # precision/false-positive trap
│       └── baseline.json         # NEW: last-known-good live scores (initial: stub)
└── tests/
    └── test_skill_evals.py       # NEW: offline scorer/loader/report tests + 1 opt-in slow live test

specs/skills/review.md             # EDIT: add "Evaluation" contract section
specs/components/skills.md         # EDIT: document the generic eval harness + registry
docs/operations/skill-evals.md     # NEW: operator doc (how to run offline/live, add cases/skills)
docs/README.md                     # EDIT: link the new doc
```

**Structure Decision**: harness in `koan/app/` (covered by
`[tool.coverage.run] source=["app"]`), per-skill data co-located with the skill
under `evals/` (data travels with the skill it evaluates), tests in the standard
`koan/tests/` location.

## Complexity Tracking

> Empty — no constitution violations to justify. The scorer is deliberately a
> small pure function; the live adapter composes three existing functions rather
> than duplicating review-pipeline logic.
