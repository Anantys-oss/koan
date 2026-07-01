# Research — Native Spec-Kit (`/speckit`) Mission Orchestration

**Feature**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Date**: 2026-06-29

Phase 0 resolves every open decision the spec deliberately deferred (and the two
integration questions that materialized during planning). Each entry records the
**Decision**, **Rationale**, and **Alternatives considered**, with code citations.

---

## R1 — Orchestration mechanism (the spec's deferred decision)

**Decision**: `/speckit` is a **mission-queuing skill** modeled on `/implement`. A
thin `handler.py` runs code-enforced pre-gates, then calls
`app.utils.insert_pending_mission` (`koan/app/utils.py:624`) to queue a **single**
Claude mission (`model_key: mission`). A prompt file (`prompts/speckit.md`, loaded via
`app.prompts.load_skill_prompt`, `koan/app/prompts.py:158`) instructs the agent to run
the speckit pipeline in sequence.

**Rationale**: This is exactly how `/implement` already turns one intent into a full
read→plan→implement→test→PR flow (`koan/skills/core/implement/handler.py:8`). It needs
**zero** `skill_dispatch.py` changes — that registry is only for skills that bypass the
Claude agent (`plan`/`rebase`/`recreate`/`check`), not mission-queuing skills. Reusing
this pattern honors Principle VII (no parallel paths) and Principle VI (missions mutate
only through the lifecycle functions).

**Alternatives considered**:
- *Combo skill (`sub_commands`)* like `plan_implement`: rejected — combos queue N
  independent missions, which cannot express "hard-abort step 1–4, best-effort 5–6"
  (the queuer never sees sub-mission outcomes) nor the single-mission observable model
  the user chose (FR-018).
- *Direct runner* in `_SKILL_RUNNERS`: rejected — the speckit steps are LLM-driven
  Claude Code skills; they require the Claude agent, so a pure-Python runner cannot
  execute them.

---

## R2 — How the agent runs the speckit sub-pipeline

**Decision**: The orchestration prompt tells the Claude agent to invoke the spec-kit
skills — `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-implement` —
in order, within the single queued mission. These skills are provided by the spec-kit
integration already scaffolded under `.specify/` (`.specify/integration.json` →
`integration: "claude"`, `invoke_separator: "-"`); they are registered Claude Code
skills, confirmed present in this session's skill list.

**Rationale**: speckit's own workflow (`.specify/workflows/speckit/workflow.yml`) defines
exactly this sequence (`specify → plan → tasks → implement` with review gates). Driving
it from inside one Claude mission keeps the observable model a single mission (FR-018)
while letting each sub-step produce its artifact (`spec.md`/`plan.md`/`tasks.md`) in the
target project tree.

**Alternatives considered**:
- *Subprocess to a `specify` CLI*: rejected — spec-kit's initialized integration here is
  the Claude integration, not a standalone CLI; shelling out would fork the mechanism.
- *Kōan reimplementing specify/plan/tasks*: rejected — duplicates spec-kit and would
  drift; Principle VII.

---

## R3 — Quota "hold" without a new pause state

**Decision**: The affordability start-gate (FR-017) rides Kōan's existing machinery.
`app.usage_tracker.UsageTracker.remaining_budget()` (`koan/app/usage_tracker.py:130`)
returns `(session_remaining_pct, weekly_remaining_pct)`. When the agent loop picks a
`/speckit` mission and `session_remaining < get_speckit_config()["quota_threshold"]`
(default 15), the mission is **left in Pending** (not started, not failed) and skipped
that iteration; it proceeds automatically on a later iteration once quota recovers. No
new state file or pause mode is introduced.

**Rationale**: This matches the user's requirement ("on hold, not aborted; proceeds
when quota recovers") using the queue Kōan already has. Principle III (no new state
files) and VII (no new pause mechanism). It is **code-enforced** at mission-pickup, so
it is load-bearing under Principle V — not merely a prompt suggestion.

**Alternatives considered**:
- *Handler refuses to queue when low*: rejected — violates "proceeds automatically when
  quota recovers" (operator would have to resend).
- *New `[quota-hold]` tag + pause_manager*: rejected — adds state and a tag-parsing
  path the existing skip-when-unaffordable behavior already covers.

**Hook point**: deferred to tasks.md — `mission_executor.py` dispatch or
`iteration_manager.py` pick-time check; both are viable.

---

## R4 — `/speckit_from_branch` git flow

**Decision** (clarified Session 2026-06-29): the handler resolves the project from
`repo-id` (via `resolve_project_path`, `koan/app/utils.py:925`) and records `branch-name`
as the **base** for a new prefixed `koan/*` implementation branch. The orchestration
prompt instructs the agent to create that branch off the human's branch (inheriting the
validated `spec.md`), **skip `specify`**, and run `plan → tasks → implement → review →
CI → PR`. The human's branch is never committed to directly (FR-014).

**Rationale**: Honors the always-prefixed-branch rule (FR-014/Principle I), inherits the
human's spec automatically, and matches the standard `/speckit` flow with the human's
branch as base instead of `main`. Keeps human and bot commits on separate branches.

**Alternatives considered**:
- *Work directly on the human's branch*: rejected — mixes bot commits onto a possibly
  non-prefixed branch; violates FR-014.
- *Fresh branch off `main`, spec only*: rejected — discards any scaffolding the human
  pushed alongside the spec; the user wants to "resume" their work.

---

## R5 — Per-task commit cadence

**Decision** (clarified Session 2026-06-29): the implement step commits **once per
`tasks.md` task** — the unit spec-kit's implement loop processes. The orchestration
prompt carries a per-task commit hint; commit messages follow the project's detected
conventions (`app.commit_conventions`, used by `rebase_pr.py`/`ci_queue_runner.py`). No
empty commits when a task produces no changes.

**Rationale**: speckit's `/speckit-implement` skill processes `tasks.md` one task at a
time, so per-task commits are the natural checkpoint and give reviewers a 1:1
commit-to-task mapping. Satisfies FR-019/SC-010 and the user's "incremental
checkpointing" intent.

**Alternatives considered**:
- *Per `plan.md` phase* (coarser): rejected by the user — fewer, larger commits; weaker
  reviewability.
- *Agent-discretion grouping*: rejected — unpredictable boundaries; not testable.

**Honesty note**: the commit cadence is **prompt-advised**, so it is advisory under
Principle V (the same tools remain available). The load-bearing guarantees (draft PR,
prefixed branch, no merge) stay code-enforced via `pr_create(draft=True)`.

---

## R6 — Constitution gate (the one new piece of logic)

**Decision**: The shared `speckit_orchestration` helper resolves the target project
path, then checks `Path(project_path) / ".specify" / "memory" / "constitution.md"`. If
absent, the handler replies with a clear error naming the file + project and queues
nothing (FR-003/FR-004). No existing helper does this — it is genuinely new.

**Rationale**: The constitution is the single readiness signal the user specified. The
check is **code-enforced** at the handler (load-bearing, Principle V), fail-fast, before
any quota or mission work.

**Alternatives considered**:
- *Also pre-check speckit availability*: rejected — the user stated the constitution is
  the sole gate; an unavailable `specify` surfaces naturally as a step-1 abort.
- *Auto-`speckit init`*: rejected — out of scope; the user said "if the constitution is
  there then we assume we can use speckit."

---

## R7 — Configuration (single read path)

**Decision**: Add one accessor `get_speckit_config()` in `koan/app/config.py` following
`get_review_reflect_config()` (`koan/app/config.py:1803`): reads the `speckit:` section
of `instance/config.yaml` with safe defaults. Keys: `quota_threshold` (int, default 15),
`review_max_iterations` (int, default 3), `review_severity` (str, default `"important"`).

**Rationale**: Principle VI — one read path per concern, never inline `os.environ`/YAML.
Centralizing the three tunables in one accessor keeps the gates and the prompt builder
reading from one place.

**Alternatives considered**: per-key accessors — rejected as needless fragmentation; one
section/one accessor matches `get_review_reflect_config()`.

---

## R8 — `specs/` directory collision (constitution TODO `SPECS_DIR_COLLISION`)

**Decision**: This feature's speckit trio stays at `specs/001-speckit-native-support/`
(spec-kit default), a sibling of Kōan's design-contract dirs `specs/components/` and
`specs/skills/`. The durable design contract for the `/speckit` skill is recorded
separately at `specs/skills/speckit.md` (Principle II). Long-term separation of one-shot
feature specs from design contracts remains a **deferred** repo-layout decision; it does
not block this feature.

**Rationale**: No filesystem collision for a single feature; the speckit tooling and
downstream commands (`/speckit-tasks`, `/speckit-implement`) key off `.specify/feature.json`
(which points here), so relocating later is non-breaking. Principle VII (start simple).

---

## Open items requiring no decision (informational)

- **SkillContext** fields available to handlers: `koan_root`, `instance_dir`,
  `command_name`, `args`, `send_message`, `handle_chat`, `project_name`, `memory`
  (`koan/app/skills.py:666`).
- **Outbox** progress notes: `app.utils.append_to_outbox(instance_dir/"outbox.md", …)`
  (`koan/app/utils.py:1126`) — used for per-step progress (FR-018).
- **Draft PR + CI reuse**: `pr_create(draft=True)` (`koan/app/github.py:134`),
  `check_ci_status` (`koan/app/ci_queue_runner.py:29`), `run_ci_fix_loop`
  (`koan/app/claude_step.py:1466`).
- **Tests**: `koan/tests/test_speckit_skill.py`, `KOAN_ROOT=/tmp/test-koan` prefix,
  mock `format_and_send`; `TestCoreSkillGroupEnforcement` enforces the mandatory
  `group:` field.
