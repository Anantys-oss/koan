---
type: skill-spec
title: "Skill Spec — brainstorm"
tags: [skill]
created: 2026-07-02
updated: 2026-07-02
---

# Skill Spec — `brainstorm`

## Command(s)

- **Primary:** `/brainstorm <topic>` · `/brainstorm <project> <topic>` · `/brainstorm <topic> --tag <label>`
- **Group:** `code`

## Purpose

Decompose a broad topic into linked GitHub sub-issues grouped under a master
tracking issue. Each sub-issue carries a structured body (why / approach /
acceptance criteria / risks / scores / priority / dependencies) so the work is
actionable and rankable.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| topic | command arg | yes | free-form area to decompose |
| project name | command arg | no | scopes the issues |
| `--tag <label>` | flag | no | GitHub label applied to the sub-issues |

## Outputs / side effects

- Generates a tag (if not provided) and ensures the GitHub label exists.
- Invokes Claude to decompose the topic into a JSON `{issues[]}` (each with
  `title` + structured `body`), plus optional synthesis (`top_ranked`,
  `fast_wins`, `overall_assessment`).
- Creates the sub-issues and a master tracking issue linking them.

## Error cases

| Condition | Behavior |
|---|---|
| empty topic | reply with usage |
| unparseable decomposition | reported; the run aborts that attempt |
| issues missing required body sections | a retry reminder is sent and regeneration is attempted |

## Integration hooks

- **Runner:** `brainstorm_runner.py` (`run_brainstorm`). **GitHub:** `github_enabled`
  + `github_context_aware`.

## Invariants

- Every issue body MUST contain all seven `REQUIRED_ISSUE_SECTIONS` (validated by
  `_validate_issue_bodies`); malformed bodies trigger a regeneration prompt.
- `_parse_decomposition` is the single JSON-extraction path (handles fences +
  preamble).

## Evaluation

The `brainstorm` skill is covered by the eval harness (`koan/app/skill_evals.py`;
design in `specs/003-core-skill-evals/`). It is the strongest analog to `review`
— both emit checkable JSON.

- **What's scored:** the decomposition — JSON validity, issue-count range
  (default 3–8), per-issue required-section coverage (reusing
  `REQUIRED_ISSUE_SECTIONS` + `_validate_issue_bodies`), priority-enum validity
  (Immediate / Prototype First / Research Further / Skip), score-bar presence
  (Impact / Difficulty / Short-Term ROI / Long-Term Value), and theme-keyword
  recall across issue titles.
- **Golden dataset:** `koan/skills/core/brainstorm/evals/cases/*.json` —
  `auth_decomposition`, `onboarding_flow`, `observability`.
- **CI:** offline scorer + dataset-validity tests run in the `fast` group and
  never call the Claude subprocess.
- **Live:** `KOAN_EVAL_LIVE=1 python -m app.skill_evals brainstorm --live` runs
  the real decomposition over the dataset and compares to `evals/baseline.json`.

**Contract:** changing `REQUIRED_ISSUE_SECTIONS`, the priority enum, the score
axes, or the `decompose.md` prompt MUST be reflected in the golden cases /
baseline.

## Known debt / watch-outs

- The synthesis keys (`top_ranked`, `fast_wins`, `overall_assessment`) are
  optional and coerced leniently — a malformed synthesis never blocks issue
  creation, and is not part of the eval contract.
