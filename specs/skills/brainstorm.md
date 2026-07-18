---
type: skill-spec
title: "Skill Spec — brainstorm"
description: "Specifies the `/brainstorm` skill, which decomposes a topic into structured, linked sub-issues (GitHub or Jira) under a master tracking issue and is covered by the skill-eval harness."
tags: [skill]
created: 2026-07-02
updated: 2026-07-18
---

# Skill Spec — `brainstorm`

## Command(s)

- **Primary:** `/brainstorm <topic>` · `/brainstorm <project> <topic>` · `/brainstorm <topic> --tag <label>`
- **Group:** `code`

## Purpose

Decompose a broad topic into linked sub-issues grouped under a master tracking
issue. Each sub-issue carries a structured body (why / approach / acceptance
criteria / risks / scores / priority / dependencies) so the work is actionable
and rankable.

The **tracker is chosen per project** by the provider-neutral `issue_tracker`
service layer (`tracker:` in `projects.yaml`) — brainstorm never branches on
GitHub vs Jira itself:

- **GitHub** (default) — sub-issues and master are GitHub Issues; `SUB-N`
  cross-references resolve to `#N`; the master's task list expresses linkage.
- **Jira** — sub-issues and master are created via the Jira REST API with the
  project's configured issue type. Bodies are rendered as **rich ADF** (headings,
  lists, checklists, rules, blockquotes, code, inline marks) rather than raw
  markdown; `SUB-N` references resolve to real Jira keys (e.g. `PROJ-42`); and the
  master is **natively linked** to each sub-issue via Jira "Linked issues"
  relationships.

Both paths run through one code path — `create_issue` / `update_issue`
(`SUB-N` resolution) / `link_issues` (master↔sub) — so the "link them properly
together" behavior is identical in shape across trackers. See
`specs/components/issue-tracking.md` for the neutral operations and the ADF
rendering / native-link contract.

See `docs/users/skills.md` for the end-user `/brainstorm` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| topic | command arg | yes | free-form area to decompose |
| project name | command arg | no | scopes the issues |
| `--tag <label>` | flag | no | GitHub label applied to the sub-issues |

## Outputs / side effects

- Generates a tag (if not provided) and ensures the GitHub label exists (labels
  are a GitHub-only nicety; Jira ignores them per `supports_labels`).
- Invokes Claude to decompose the topic into a JSON `{issues[]}` (each with
  `title` + structured `body`), plus optional synthesis (`top_ranked`,
  `fast_wins`, `overall_assessment`).
- Creates the sub-issues and a master tracking issue linking them, via the
  provider-neutral `issue_tracker` service.
- Resolves `SUB-N` placeholders in sub-issue bodies to the real created refs
  (`#N` on GitHub, `PROJ-N` on Jira) via `update_issue`; per-issue failures are
  logged and skipped (non-fatal).
- On Jira, renders bodies as rich ADF and creates native master↔sub links via
  `link_issues`; on GitHub `link_issues` is a no-op. Link failures are logged and
  skipped (non-fatal).

## Error cases

| Condition | Behavior |
|---|---|
| empty topic | reply with usage |
| unparseable decomposition | reported; the run aborts that attempt |
| issues missing required body sections | a retry reminder is sent and regeneration is attempted |

## Integration hooks

- **Runner:** `brainstorm_runner.py` (`run_brainstorm`). **GitHub:** `github_enabled`
  + `github_context_aware`.
- **Tracker routing:** the `app.issue_tracker` service layer
  (`create_issue` / `update_issue` / `link_issues`) — never a direct `gh`/Jira
  call in the runner. Provider is resolved per project from `projects.yaml`.

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
