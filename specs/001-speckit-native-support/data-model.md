# Data Model — Native Spec-Kit (`/speckit`) Mission Orchestration

**Feature**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

This feature adds **no new persistent storage** (constitution Principle III: no new
state files). The "data model" here is the **in-memory shape** of a `/speckit`
invocation, the **configuration keys**, and the **mission lifecycle states** the feature
rides on. All file mutations flow through existing atomic helpers.

---

## Entities

### SpeckitMission (in-memory, per invocation)

The resolved representation a handler builds before queuing.

| Field | Type | Source | Notes |
|---|---|---|---|
| `entry_mode` | enum: `agent` \| `from_branch` | trigger surface | `agent` runs `specify…`; `from_branch` skips it (FR-020). |
| `project_path` | path | `resolve_project_path` | Target project filesystem root. |
| `project_name` | str | `project_name_for_path` | Used for the `[project:name]` tag. |
| `goal_text` | str | chat arg / issue body+comments | The "problem to solve" (FR-005). `None` for `from_branch` (spec already exists). |
| `issue_ref` | url\|key \| None | trigger | For issue/`@mention` surfaces; linked from the PR. |
| `repo_override` | str \| None | `repo:` token | Parsed + stripped from goal text (FR-007). |
| `branch_override` | str \| None | `branch:` token / `branch-name` arg | Base branch for `from_branch`; target hint otherwise. |
| `base_branch` | str \| None | `from_branch` only | The human's validated-spec branch a new `koan/*` branch is cut from. |

**Identity / uniqueness**: a mission is identified by its `missions.md` entry text. The
standard queue handles concurrency (distinct missions run one at a time, each its own
branch/PR); dedup (skip) applies only to the identical issue with an open PR (matches
`/implement`).

**Validation rules** (enforced in `speckit_orchestration` before queuing):
- `project_path` MUST resolve (else usage error).
- Constitution MUST exist at `project_path/.specify/memory/constitution.md` (else abort
  with actionable error — FR-003).
- `from_branch` MUST supply both `repo-id` and `branch-name` (else usage error).

### ConstitutionGate (code-enforced, stateless)

A predicate, not a stored object: `exists(project_path/.specify/memory/constitution.md)`
→ bool. Load-bearing (Principle V). Fail-fast at the handler; never bypassed by any
entry mode.

### PipelineStepOutcome (transient)

Per-step result the orchestration prompt reports: `specify|plan|tasks|implement|review|ci|pr`
× `ok|failed|skipped|held`. Drives the observable contract: hard-abort on a `failed`
step 1–4 (for the steps that run); best-effort on 5–6.

---

## Configuration (`instance/config.yaml` → `get_speckit_config()`)

Single read path (Principle VI), accessor in `koan/app/config.py` modeled on
`get_review_reflect_config()`.

| Key | Type | Default | Used by | FR |
|---|---|---|---|---|
| `quota_threshold` | int (0–100) | `15` | quota start-gate (R3) | FR-017 |
| `review_max_iterations` | int (≥0) | `3` | private review→fix loop | FR-009 |
| `review_severity` | str | `"important"` | severity floor for review fixes | FR-009 |

Example:
```yaml
speckit:
  quota_threshold: 15
  review_max_iterations: 3
  review_severity: important
```

---

## State transitions (ridden, not invented)

The feature rides the existing `missions.md` lifecycle (`app.missions`). No new states.

```
operator triggers /speckit
   │
   ▼
[handler: code-enforced gates]
   ├── constitution missing ──► reply + queue nothing            (abort, FR-003)
   ├── quota < threshold      ──► queue Pending + skip-on-pick     (hold,  FR-017)
   └── gates pass             ──► insert_pending_mission            (Pending)
   ▼
[agent loop picks mission]
   ├── quota still < threshold ──► leave Pending, retry later      (held)
   └── affordable              ──► start_mission                    (In Progress)
   ▼
[orchestration prompt drives the pipeline]
   ├── step 1–4 fails ──► agent reports + fail_mission             (Failed, FR-008) — artifacts preserved
   ├── steps 5–6 fail ──► continue (best-effort)                   (no abort, FR-011)
   └── pipeline done  ──► pr_create(draft=True) + reply w/ link    (Done, FR-012/014)
```

**Invariants preserved**:
- `missions.md` mutated **only** via `insert_pending_mission` / `start_mission` /
  `complete_mission` / `fail_mission` / `requeue_mission` (Principle VI).
- Draft PR on a prefixed branch, never merged (Principle I).
- Outbound PR/commit content scanned; inbound goal text treated as untrusted data
  (Principle V).
