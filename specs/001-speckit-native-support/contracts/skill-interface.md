# Contract — `/speckit` Skill Interface

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md)

The interface this feature exposes is the **skill command surface** (the appropriate
contract format for a Kōan CLI-tool skill, per the plan template). It defines what
operators, the `@mention` pipeline, and the mission queue can invoke, and the
frontmatter flags that wire it into Kōan's systems.

---

## Commands

### `/speckit` — full spec-driven run (specify → … → draft PR)

```
/speckit <project> <goal>
/speckit <issue-url> [repo:<repo> branch:<branch>] [extra context]
@bot /speckit [on a GitHub/Jira issue thread]
```

- **Purpose**: turn an operator intent (or an issue) into a fully specced, implemented,
  draft-PR feature by running the speckit pipeline then Kōan's review/CI/PR machinery.
- **Required arg**: a project name **or** an issue URL (else usage reply, queue nothing).
- **Optional**: `repo:<repo>` / `branch:<branch>` overrides (parsed + stripped, FR-007);
  trailing context folded into the goal text.
- **`@mention` form**: `@bot /speckit` (and bare `@bot speckit`) on an issue thread;
  issue title + body + all comments become the goal text. Subject to the existing
  permission check; the bot reacts to acknowledge.

### `/speckit_from_branch` — resume from a human-validated spec on a branch

```
/speckit_from_branch <repo-id> <branch-name>
```

- **Purpose**: the spec already exists (human-authored, validated, pushed to
  `branch-name`); **skip `specify`** and run `plan → tasks → implement → review → CI →
  PR` against it.
- **Required args**: `repo-id` (project name / `owner/repo` / repo URL — resolved via
  `resolve_project_path`) and `branch-name` (the validated-spec branch).
- **Branch contract**: Kōan creates a new prefixed `koan/*` branch **based off
  `branch-name`** (inheriting the spec) and opens the draft PR from it. The human's
  branch is never committed to directly (FR-014/FR-020).

---

## SKILL.md frontmatter contract (both skills)

Per `specs/components/skills.md` and the group-enforcement test.

| Field | `speckit` | `speckit_from_branch` | Why |
|---|---|---|---|
| `group` | `code` | `code` | Mandatory; drives `/help`. |
| `commands` | `speckit` | `speckit_from_branch` | Underscore names (Telegram word-boundary rule). |
| `model_key` | `mission` | `mission` | Heavy, multi-step → heavier model tier. |
| `github_enabled` | `true` | `true` | Triggerable via GitHub `@mention` (Jira reuses it). |
| `github_context_aware` | `true` | `true` | Accepts context after the command / thread body. |
| `handler` | `handler.py` | `handler.py` | Thin; delegates to `speckit_orchestration`. |

**Invariants** (from `specs/components/skills.md`):
- Names/aliases/dirs use **underscores, never hyphens**.
- No hardcoded skill-name lists in `koan/app/` — `speckit_orchestration` is the feature's
  own module, not a name-registry.
- `skill_dispatch.py` is **unchanged** (mission-queuing skill, like `/implement`).

---

## Behavioral contract (observable, from the spec)

| Concern | Contract | FR/SC |
|---|---|---|
| Constitution gate | Abort early with actionable error if target project lacks `.specify/memory/constitution.md`; zero speckit steps run. | FR-003, SC-002 |
| Quota start-gate | If remaining quota < `quota_threshold` (default 15), queue but hold (Pending); proceed automatically on recovery; never abort for low quota. | FR-017, SC-008 |
| Pipeline order | `specify → plan → tasks → implement` (`from_branch` skips `specify`). | FR-006, FR-020 |
| Hard abort | Steps 1–4 failure → fail the mission with a step-specific reason; partial artifacts preserved. | FR-008, SC-001 |
| Best-effort | Review (5) and CI (6) failures never abort; unresolved findings summarized in the PR. | FR-011, FR-013, SC-003 |
| Commits | One commit per `tasks.md` task during implement (no empty commits). | FR-019, SC-010 |
| Single mission | One `missions.md` entry; per-step progress notes to the originating channel. | FR-018 |
| Final artifact | Draft PR bundling spec/plan/tasks + implementation; prefixed branch; never merged. | FR-012, FR-014 |

---

## Dependencies on other systems

- **spec-kit skills** (`speckit-specify`, `-plan`, `-tasks`, `-implement`): provided by
  the `.specify/` Claude integration; invoked by the agent mid-mission. If unavailable,
  surfaces as a step-1 abort.
- **Issue tracker** (`issue_tracker` + `fetch_thread_context`): provides issue body +
  comments for issue/`@mention` triggers.
- **GitHub** (`gh` via `pr_create`, `check_ci_status`): draft PR + CI validation in the
  target project.
