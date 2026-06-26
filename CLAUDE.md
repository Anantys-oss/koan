# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Kōan

Kōan is an autonomous background agent that uses idle Claude API quota to work on local projects. It runs as a continuous loop, pulling missions from a shared file, executing them via Claude Code CLI, and communicating progress via Telegram. Philosophy: "The agent proposes. The human decides." — no unsupervised code modifications.

## Documentation first

- Before planning or implementing a feature or important refactor, inspect the relevant documentation with `grep`, `find`, or equivalent search. Start at `docs/README.md`, then read the matching pages under `docs/architecture/`, `docs/users/`, `docs/providers/`, `docs/messaging/`, `docs/operations/`, `docs/design/`, `docs/security/`, or `docs/setup/`.
- Treat docs as context to verify against code, not as unquestioned truth. If code and docs disagree, preserve current code behavior unless the task says otherwise, and update the docs to match the resulting behavior.
- After changing user behavior, configuration, daemon flow, provider behavior, shared state, safety boundaries, or an important implementation decision, update the relevant docs in the same branch.
- For core skill changes, update both `docs/users/user-manual.md` and `docs/users/skills.md`.

## Commands

```bash
make setup          # Create venv, install dependencies
make start          # Start full stack (auto-detects provider: awake+run or ollama+awake+run)
make stop           # Stop all running processes (run + awake + ollama)
make status         # Show running process status
make logs           # Watch live output from all processes + agent progress
make run            # Start main agent loop (foreground)
make awake          # Start Telegram bridge (foreground)
make ollama         # Start full Ollama stack (ollama serve + awake + run)
make dashboard      # Start Flask web dashboard (port 5001)
make lint           # Run ruff linter (must pass before committing)
make test           # Run full test suite (pytest + coverage summary)
make coverage       # Run tests with detailed coverage report (HTML in htmlcov/)
make say m="..."    # Send test message as if from Telegram
make rename-project old=X new=Y [apply=1]  # Rename a project everywhere (dry-run by default)
make clean          # Remove venv
```

Run a single test file:

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_missions.py -v
```

## Test suite

- **`KOAN_ROOT` must be set** when running tests. Many modules (`utils.py`, `awake.py`) check for `KOAN_ROOT` at import time and raise `SystemExit` if it's missing. Use `KOAN_ROOT=/tmp/test-koan` (or any path) as a prefix: `KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/ -v`
- Never call Claude (subprocess) in tests. Mock `format_and_send` which invokes Claude CLI for message formatting.
- With `runpy.run_module()` (CLI tests), patch both `app.<module>.format_and_send` **and** `app.notify.format_and_send` — `runpy` re-executes the module so the import-level binding escapes the first patch.
- When `load_dotenv()` would reload env vars from `.env` (defeating `monkeypatch.delenv`), patch `app.notify.load_dotenv` too.
- **Test behavior, not implementation.** Unless the project's own conventions say otherwise, tests should validate what code does (inputs → outputs, side effects, observable state), not how it does it. Mocking internal dependencies of the unit under test is fine, but tests must never read or inspect actual source code to verify whether specific code is present or absent — that couples tests to implementation text rather than behavior. Prefer asserting on return values, raised exceptions, file contents, or other observable outcomes.
- **Mock above retry_with_backoff, not below.** When testing error handling for `run_gh()`/`api()` callers, mock at the `run_gh` or `api` level — never at `app.github.subprocess.run`. Mocking subprocess.run causes `retry_with_backoff` to sleep 1+2+4s between retries, adding 7+ seconds per test. See `testing-anti-patterns.md` Anti-Pattern 6.

## Architecture

Two parallel processes run independently:

- **`awake.py`** (Telegram bridge): Polls Telegram every 3s. Classifies messages as "chat" (instant Claude reply) or "mission" (queued to `missions.md`). Flushes `outbox.md` messages back to Telegram. Command handling is split into `command_handlers.py`, shared state in `bridge_state.py`, colored log output in `bridge_log.py`.
- **`run.py`** (agent loop): Pure-Python main loop with restart wrapper. Core execution host: `run_claude_task()` (CLI subprocess invocation and monitoring), `_finalize_mission()` (lifecycle state machine: Done/Failed/requeue), `_classify_and_handle_cli_error()` (error → action mapping), and `_probe_exit0_quota()` (false-success detection). Signal handling uses double-tap CTRL-C protection (`protected_phase` context manager). Writes real-time status to `.koan-status`. Per-iteration dispatch delegated to `mission_executor.py`; stateless pipeline helpers delegated to `mission_runner.py`.

Communication between processes happens through shared files in `instance/` with atomic writes (`utils.atomic_write()` using temp file + rename + `fcntl.flock()`). Exclusive process instances enforced via `pid_manager.py` (PID file + `fcntl.flock()`).

### Key modules (`koan/app/`)

**Core data & config:**

- **`missions.py`** — Single source of truth for `missions.md` parsing (sections: Pending / In Progress / Done; French equivalents also accepted). Missions can be tagged `[project:name]`. Provides explicit lifecycle transitions: `start_mission()` (Pending→In Progress with stale-flush sanity enforcement), `complete_mission()`, `fail_mission()`.
- **`projects_config.py`** — Project configuration loader for `projects.yaml`. `load_projects_config()`, `get_projects_from_config()`, `get_project_config()` (merged defaults + overrides), `get_project_auto_merge()`, `get_project_cli_provider()`, `get_project_models()`, `get_project_tools()`. Per-project overrides for CLI provider, model selection, and tool restrictions. `ensure_github_urls()` auto-populates `github_url` fields from git remotes at startup.
- **`projects_migration.py`** — One-shot migration from env vars (`KOAN_PROJECTS`/`KOAN_PROJECT_PATH`) to `projects.yaml`. Runs at startup if `projects.yaml` doesn't exist.
- **`utils.py`** — File locking (thread + file locks), config loading, atomic writes, `get_branch_prefix()`, `get_known_projects()` (projects.yaml > KOAN_PROJECTS), `koan_tmp_dir()` (per-uid scratch/lock dir)
- **`config.py`** — Centralized configuration loading and access: tool config, model selection, Claude CLI flag building, behavioral settings, auto-merge config
- **`constants.py`** — Centralized numeric constants for the agent loop (thresholds, timeouts, tuning parameters). Import-as pattern preserves module-level attribute names for test compatibility.
- **`run_log.py`** — Shared colored logging wrapper (`log_safe(category, msg)`). Replaces per-module `_log_*` helpers.
- **`commit_conventions.py`** — Project commit convention detection and parsing. `get_project_commit_guidance()` reads CLAUDE.md commit-related sections or infers conventions from recent commit history. `parse_commit_subject()` extracts `COMMIT_SUBJECT:` markers from Claude output. Used by `rebase_pr.py` and `ci_queue_runner.py` to produce convention-aware commit messages.

**Agent loop pipeline** (called from `run.py`):

- **`iteration_manager.py`** — Per-iteration decision-making: usage refresh, mode selection, recurring injection, mission picking, project resolution.
- **`mission_executor.py`** — Per-iteration dispatch layer extracted from `run.py`. Contains `_run_iteration()` (full iteration orchestration: pick mission → dispatch → execute → finalize), `_handle_skill_dispatch()` (slash-command routing), and `_maybe_retry_mission()` (single transient-error retry). Calls back into `run.py` for `run_claude_task()` and `_finalize_mission()`.
- **`mission_runner.py`** — Execution pipeline helpers: `build_mission_command()` (CLI prompt + flags), `parse_claude_output()` (JSON → text extraction), and post-mission processing (usage tracking, pending.md archival, reflection, auto-merge). Called by `mission_executor.py` and `run.py`.
- **`loop_manager.py`** — Focus area resolution, pending.md creation, interruptible sleep with wake-on-mission, project validation
- **`contemplative_runner.py`** — Contemplative session runner (probability roll, prompt building, CLI invocation)
- **`quota_handler.py`** — Quota exhaustion detection from CLI output; parses reset times, creates pause state, writes journal entries
- **`prompt_builder.py`** — Agent prompt assembly for the agent loop. Includes budget-aware context trimming.
- **`event_scheduler.py`** — One-shot datetime-scheduled mission triggers. Reads `instance/events/*.json`, fires missions on schedule.
- **`suggestion_engine.py`** — Automation suggestion engine: surfaces recurring/schedule system recommendations with copy-pasteable commands
- **`pr_review_learning.py`** — Extracts actionable lessons from human PR reviews using Claude CLI (lightweight model). Fetches review data from GitHub, sends raw comments to Claude for natural-language analysis, and persists new lessons to `memory/projects/{name}/learnings.md` (write-once, read-many). Uses content-hash caching to skip re-analysis when reviews haven't changed.
- **`review_comment_dispatch.py`** — Automatic mission dispatch when human reviewers leave comments on Koan's open PRs. `fetch_unresolved_review_comments()` gathers unresolved inline + review-body comments (bot-filtered), `compute_comment_fingerprint()` produces a SHA-256 dedup key, and `check_and_dispatch_review_comments()` inserts a mission only when the fingerprint changes (tracked in `.review-dispatch-tracker.json`). Wired into `process_github_notifications()` in `loop_manager.py`. Opt-in via `review_dispatch: { enabled: true }` in `config.yaml`.
- **`skill_dispatch.py`** — Direct skill execution from agent loop. Detects `/command` missions, parses project prefix and command, dispatches to skill-specific runners (plan, rebase, recreate, check, claudemd) bypassing the Claude agent. Note: skill runners emit structured agent transcripts to stdout (DATA), not raw CLI output. `mission_executor.py` already passes `trust_stdout=False` to `_classify_and_handle_cli_error()` for these dispatches so the transcript text isn't mistaken for a quota/auth error message — keep that default when adding new dispatch pathways; individual runners do not call the classifier themselves.
- **`stagnation_monitor.py`** — Daemon thread that hashes the last N lines of Claude CLI stdout at configurable intervals. After K consecutive identical hashes, kills the subprocess group so a stuck-in-a-loop session does not burn quota for the full `mission_timeout`. Wired into `run_claude_task()`; stagnated missions are re-queued to Pending up to `max_retry_on_stagnation` times (per-mission counter persisted in `instance/.stagnation-retries.json`) before being tagged `[stagnation]` in `missions.md` and triggering the regular `_notify_stagnation()` Telegram warning. Each requeue sends a separate `_notify_stagnation_retry()` message.
- **`hooks.py`** — Hook system for extensible lifecycle events. Discovers `.py` modules from `instance/hooks/`, registers handlers by event name, fires them sequentially with per-handler error isolation. Events: `session_start`, `session_end`, `pre_mission`, `post_mission`.
- **`devcontainer.py`** — Devcontainer execution support. Detects spec-defined config locations (`is_devcontainer_present()`), resolves the container workspace path (`_get_container_workspace_path()` via `devcontainer read-configuration` with manual JSON fallback), brings the container up with feature injection and bind-mounts (`ensure_container_up()`), runs post-start git credential setup (`_run_container_setup()`), and wraps CLI commands with `devcontainer exec` prefix while translating host tmp paths to container paths (`wrap_command()`). Enabled per-project via `devcontainer: true` in `projects.yaml`. Provider-aware: the three `ghcr.io` features and the `gh auth login` credential step are claude-only.

**Bridge (Telegram):**

- **`awake.py`** — Main bridge loop, Telegram polling, outbox flushing
- **`command_handlers.py`** — Telegram command handlers extracted from awake.py; core commands (help, stop, pause, resume, skill) + skill dispatch
- **`bridge_state.py`** — Shared module-level state for bridge (config, paths, registries); avoids circular imports
- **`bridge_log.py`** — Colored log output for bridge process (mirrors run.py's `log()`)
- **`notify.py`** — Telegram notification helper with flood protection

**Process management:**

- **`pid_manager.py`** — Exclusive PID file enforcement for run, awake, and ollama processes. Provides `start_all()` (unified stack launcher with provider auto-detection), `start_runner()`, `start_awake()`, `start_ollama()`, and `stop_processes()` (graceful SIGTERM with force-kill fallback)
- **`pause_manager.py`** — Pause state management (`.koan-pause` / `.koan-pause-reason` files). Supports time-bounded pauses with auto-resume (e.g., `/pause 2h`)
- **`restart_manager.py`** — File-based restart signaling between bridge and run loop (`.koan-restart`)
- **`focus_manager.py`** — Focus mode management (`.koan-focus` JSON); skips contemplative sessions when active
- **`passive_manager.py`** — Passive mode management (`.koan-passive` JSON); read-only mode that blocks all execution while keeping loop alive

**CLI provider abstraction** (`koan/app/provider/`):

- **`provider/base.py`** — `CLIProvider` base class + tool name constants + per-provider usage tracking hooks (`supports_usage_tracking()`, `record_usage()`)
- **`provider/claude.py`** — `ClaudeProvider` (Claude Code CLI)
- **`provider/cline.py`** — `ClineProvider` (Cline CLI)
- **`provider/copilot.py`** — `CopilotProvider` (GitHub Copilot CLI) with tool name mapping
- **`provider/__init__.py`** — Provider registry, resolution (env → config → default), cached singleton, and convenience functions (`run_command()`, `run_command_streaming()`, `build_full_command()`). Main entry point for the provider package.
- **`cli_provider.py`** — Re-export facade (legacy); prefer importing from `provider` directly

**Git & GitHub:**

- **`git_sync.py`** / **`git_auto_merge.py`** — Branch tracking, sync awareness, configurable auto-merge. Branch cleanup is time-throttled (default 24h per project, persisted in `.branch-cleanup-tracker.json`). Orphan branch detection (unmerged, no open PR) notifies via outbox.
- **`github.py`** — Centralized `gh` CLI wrapper (`run_gh()`, `pr_create()`, `issue_create()`)
- **`github_url_parser.py`** — Centralized GitHub URL parsing for PRs and issues
- **`github_skill_helpers.py`** — Shared helpers for GitHub-related skills (URL extraction, project resolution, mission queuing)
- **`github_config.py`** — GitHub notification config helpers (`get_github_nickname()`, `get_github_commands_enabled()`, `get_github_authorized_users()`)
- **`github_notifications.py`** — GitHub notification fetching, @mention parsing, reaction-based deduplication, permission checks
- **`github_command_handler.py`** — Bridges GitHub @mention notifications to missions: validate command → check permissions → react → create mission
- **`github_webhook.py`** — Opt-in push-based notification triggering (default off). A stdlib `http.server` receiver (started in the bridge via `maybe_start_from_config()`, or standalone via `make webhook`) verifies the HMAC-SHA256 signature, filters to known repos + actionable event types, and writes the `.koan-check-notifications` signal so the run loop performs an immediate forced poll — collapsing the 60-180s polling latency to ~10s. Reuses the full polling pipeline; polling remains the reliability fallback. Secret via `KOAN_GITHUB_WEBHOOK_SECRET`. See `docs/messaging/github-webhooks.md`.
- **`rebase_pr.py`** — PR rebase workflow
- **`recreate_pr.py`** — PR recreation: fetch metadata/diff, create fresh branch, reimplement from scratch
- **`claude_step.py`** — Shared helpers for git operations and Claude CLI invocation (used by pr_review, rebase_pr, recreate_pr). Also provides `run_ci_fix_loop()` — shared CI fix loop with configurable recheck semantics (polling vs single-shot) via `use_polling` flag and caller-specific `prompt_builder` callable.
- **`remote_rename_detector.py`** — Detects and fixes renamed GitHub remotes in workspace projects
- **`head_tracker.py`** — Detects remote HEAD branch changes (e.g. master → main) and updates local workspace. State persisted in `instance/.head-tracker.json`, throttled to once per 12h. Integrated into startup, manual trigger via `/rescan`.

**Issue tracking** (`koan/app/issue_tracker/`):

- **`issue_tracker/base.py`** — `IssueTracker` ABC: provider-neutral contract for fetch/comment/create operations
- **`issue_tracker/config.py`** — Per-project tracker routing (`get_tracker_for_project()`), Jira key → project mapping, code repository resolution. Configured via `tracker:` section in `projects.yaml` per-project overrides.
- **`issue_tracker/github.py`** — `GitHubIssueTracker` — GitHub Issues/PRs backend via `gh` CLI
- **`issue_tracker/jira.py`** — `JiraIssueTracker` — Jira backend via REST API
- **`issue_tracker/types.py`** — Shared data types (`IssueRef`, `IssueContent`)
- **`issue_tracker/enrichment.py`** — PR-review issue context enrichment. Parses tracker references (`PROJ-123` Jira keys / `owner/repo#123` cross-repo GitHub refs) out of a PR body, fetches a short summary via the project's configured provider, and returns a capped `{ISSUE_CONTEXT}` block for the review prompt. Best-effort: every path returns `""` on failure. Gated by `review_issue_context.enabled` (default on) and wired into `review_runner.build_review_prompt()`.
- **`issue_tracker/__init__.py`** — Service layer: `fetch_issue()`, `add_comment()`, `create_issue()`, `find_existing_plan_issue()`. Callers use these instead of branching on GitHub vs Jira.
- **`issue_cli.py`** — CLI entry point for issue tracker operations (fetch, comment, create) — used by prompts and subprocesses
- **`notification_config.py`** — Shared notification polling configuration helpers (interval resolution across GitHub/Jira providers)

**Other:**

- **`memory_manager.py`** — Per-project memory isolation, compaction, and cleanup. Includes semantic learnings compaction (Claude-powered dedup/merge), global memory file rotation, and configurable thresholds via `config.yaml` `memory:` section. Dual-writes to SQLite FTS5 index alongside JSONL truth log. `read_memory_window()` supports FTS5-ranked two-phase retrieval (relevance + recency fill).
- **`memory_db.py`** — SQLite FTS5 secondary index over the JSONL memory truth log. Provides `ensure_db()`, `insert_entry()`, `search_entries()` (BM25-ranked), `search_learnings()` (transient in-memory FTS5), `recent_entries()`, `delete_before()`, and `migrate_jsonl_to_sqlite()`. All functions catch `DatabaseError` and return empty results. Graceful degradation when FTS5 unavailable.
- **`usage_tracker.py`** — Per-provider budget tracking; decides autonomous mode (REVIEW/IMPLEMENT/DEEP/WAIT) based on each provider's independent quota percentage. Pure parser + threshold class — burn-rate-driven downgrades live in `iteration_manager._downgrade_if_burning_fast` next to the existing affordability downgrade.
- **`burn_rate.py`** — Rolling burn-rate estimator (% session quota per minute). Maintains a 20-sample circular buffer in `instance/.burn-rate.json` with `fcntl.flock(LOCK_SH)` on reads, exposes `record_run()`, `burn_rate_pct_per_minute()` (total cost / span across all samples), `time_to_exhaustion(session_pct, mode=None)`, and the canonical `MODE_MULTIPLIERS` table shared with `usage_tracker.can_afford_run`. Also tracks the last-warning timestamp so the iteration manager fires at most one Telegram alert per quota cycle.
- **`recover.py`** — Crash recovery for stale in-progress missions
- **`prompts.py`** — System prompt loader; `load_prompt()` for `koan/system-prompts/*.md`, `load_skill_prompt()` for skill-bound prompts. Supports `{@include partial-name}` directive for reusable prompt fragments from `koan/system-prompts/_partials/`.
- **`skill_manager.py`** — External skill package manager: install from Git repos, update, remove, track via `instance/skills.yaml`
- **`claudemd_refresh.py`** — CLAUDE.md refresh pipeline: gathers git context, invokes Claude to update/create CLAUDE.md. When CLAUDE.md is missing, dispatches the built-in `/init` skill instead of a generic prompt.
- **`update_manager.py`** — Kōan self-update: stash, checkout main, fetch/pull from upstream, report changes
- **`auto_update.py`** — Automatic update checker and self-commit tracker. Periodically fetches upstream, triggers pull + restart when new commits are available. Also tracks Kōan's own HEAD across startups — records current SHA in `instance/.commit-tracker.json`, reports new commits via Telegram on subsequent startups. Configurable via `auto_update` section in `config.yaml` (`enabled`, `check_interval`, `notify`)
- **`ci_dispatch.py`** — Auto-dispatch fix missions when CI fails on Koan-authored PRs. Checks open PRs by branch prefix, fetches check-run status via GitHub API, inserts fix missions with log snippets. Dedup via `.ci-dispatch-tracker.json` keyed by PR+SHA+job. Configurable via `ci_dispatch` section in `config.yaml` (`enabled`, `cooldown_minutes`, `log_snippet_bytes`).
- **`security_review.py`** — Differential security review on mission diffs: blast radius analysis, risk classification, journal logging. Runs before auto-merge decisions.
- **`rename_project.py`** — CLI tool to rename a project across `projects.yaml` and all `instance/` files (missions, memory dir, journal files, JSON references). Dry-run by default, `--apply` to execute. Invoked via `make rename-project old=X new=Y [apply=1]`.
- **`usage_service.py`** — Shared usage-payload builder (`build_usage_payload()` + week/month bucketing) used by both the dashboard and the REST API (`GET /v1/usage`).
- **`log_reader.py`** — Shared log-tailing helpers (`tail_log()`, `read_logs()`) used by both the dashboard and the REST API (`GET /v1/logs`).

**Web dashboard** (`koan/app/dashboard/`):

- **`dashboard/`** — Flask blueprint package built by a `create_app()` factory (mirrors `api/__init__.py`). Blueprints: `core` (index, auth, status/health/forecast/provider), `missions` (mission CRUD + attention), `chat` (chat + progress/state SSE), `usage` (usage/metrics/efficiency/journal/logs), `agent` (soul/memory/skills/config + pause/resume/restart), `config` (config/nickname/rules/recurring), `prs` (PRs + plans). Runnable entry: `app/dashboard/__main__.py` (used by `make dashboard` and `pid_manager.start_dashboard()`). `from app.dashboard import app` exposes the module-level instance for the test suite.
  - **`dashboard/state.py`** — Single home for patchable module globals (paths, `CHAT_TIMEOUT`, `DASHBOARD_PWD`, caches, regexes). Route/service code reads `state.X` at call time so tests patch one target (`patch.object(app.dashboard.state, …)`).
  - **`dashboard/_helpers.py`** — Cross-cutting Flask wiring: passphrase gate, static cache-buster, context processor, template filters (`strip_project_tag`, `project_badge`, `linkify`); attached via `register_helpers(app)`.
- **`dashboard_service/`** — Pure business logic extracted from the routes, unit-tested without a Flask client: `missions` (parse/filter/project+skill names), `journal` (date/day readers + rule history), `plans` (plan-issue fetch + progress parsing), `stats` (forecast, skill metrics, agent-state readers); package-level `read_file`/`mask_sensitive`/`validate_yaml`. Dashboard templates live under `koan/templates/dashboard/`.

**REST API** (`koan/app/api/`):

- **`api/__init__.py`** — `create_app()` Flask factory; registers blueprints, health endpoint, JSON error handlers, per-request audit logging.
- **`api/auth.py`** — `require_token` decorator (Bearer parse + `hmac.compare_digest`); token resolution (env → config).
- **`api/mission_index.py`** — Sidecar reader/writer for `instance/.api-missions.json` (atomic via `utils.atomic_write_json`). `record_mission()`, `get_mission()`, `list_missions()`, `reconcile()` (maps stored text → current `missions.md` section), `cancel_mission()`.
- **`api/routes_missions.py`** — `GET/POST /v1/missions`, `GET/DELETE /v1/missions/{id}`.
- **`api/routes_projects.py`** — `GET /v1/projects`, `POST /v1/projects`, `DELETE /v1/projects/{name}`.
- **`api/routes_status.py`** — `GET /v1/status` (agent state + mission counts from signal files).
- **`api/routes_admin.py`** — `POST /v1/pause`, `POST /v1/resume`, `GET /v1/config` (secrets masked), `POST /v1/restart`, `POST /v1/shutdown`, `POST /v1/update`.
- **`api/routes_observability.py`** — `GET /v1/usage`, `GET /v1/metrics`, `GET /v1/logs` (token-gated; delegate to usage_service / mission_metrics / log_reader).
- **`api/server.py`** — Runnable entrypoint (`make api`); validates token at startup (fail-closed), warns on non-loopback bind, calls `waitress.serve(create_app(), ...)`.

Config additions in `config.py`: `is_api_enabled()`, `get_api_host()` (default `127.0.0.1`), `get_api_port()` (default `8420`), `get_api_token()` (env `KOAN_API_TOKEN` → `api.token` → `""`), `get_api_threads()` (default `8`). `pid_manager.py` adds `"api"` to `PROCESS_NAMES` and provides `start_api()` / `_is_api_enabled()`. See `docs/operations/rest-api.md`.

### Skills system (`koan/skills/`)

Extensible command plugin system. Each skill lives in `skills/<scope>/<skill-name>/` with a `SKILL.md` (YAML frontmatter defining commands, aliases, metadata) and an optional `handler.py`.

- **`skills.py`** — Registry that discovers SKILL.md files, parses frontmatter (custom lite YAML parser, no PyYAML), maps commands/aliases to skills, and dispatches execution.
- **Core skills** live in `koan/skills/core/` (abort, add_project, ai, alias, ask, audit, audit_all, autoreview, brainstorm, branches, brief, cancel, changelog, chat, check, check_need, check_notifications, checkup, ci_check, claudemd, config_check, dead_code, debug, deep, deepplan, delete_project, diagnose, doc, doctor, done, email, explain, explore, fix, focus, gh, gh_request, gha_audit, idea, implement, inbox, incident, journal, language, list, live, logs, magic, messaging_level, mission, models, orphans, passive, plan, plan_implement, pr, priority, private_security_audit, profile, projects, quota, rebase, recreate, recurring, refactor, reflect, rename, report, rescan, reset, restart, review, review_rebase, rtk, scaffold_skill, security_audit, shutdown, snapshot, sparring, spec_audit, squash, stats, status, tech_debt, time, tracker, ultrareview, verbose, version)
- **Custom skills** loaded from `instance/skills/<scope>/` — each scope directory can be a cloned Git repo for team sharing.
- **Handler pattern**: `def handle(ctx: SkillContext) -> Optional[str]` — return string for Telegram reply, empty string for "already handled", None for no message.
- **`worker: true`** flag in SKILL.md marks blocking skills (Claude calls, API requests) that run in a background thread.
- **`github_enabled: true`** flag marks skills that can be triggered via GitHub @mentions. **`github_context_aware: true`** means the skill accepts additional context after the command.
- **Combo skills**: `sub_commands` field in SKILL.md frontmatter defines skills that decompose into multiple sub-missions (e.g., `/review_rebase` queues both `/review` and `/rebase`). `collect_combo_skills()` in `skills.py` discovers these dynamically from the registry.
- **Prompt-only skills**: omit `handler`, put prompt text after the frontmatter — sent to Claude directly.
- See `koan/skills/README.md` for the full authoring guide.

### Instance directory

`instance/` (gitignored, copy from `instance.example/`) holds all runtime state:

- `missions.md` — Task queue
- `outbox.md` — Bot → Telegram message queue (written atomically by `append_to_outbox()`)
- `outbox-sending.md` — Crash-safety staging file for outbox flush; `OutboxManager.recover_staged()` re-sends on restart
- `config.yaml` — Per-instance configuration (tools, auto-merge rules)
- `soul.md` — Agent personality definition
- `memory/` — Global summary + per-project learnings/context + `memory.db` (SQLite FTS5 index)
- `journal/` — Daily logs organized as `YYYY-MM-DD/project.md`
- `events/` — One-shot scheduled missions (JSON files consumed by `event_scheduler.py`)
- `hooks/` — User-defined Python hook modules for lifecycle events (see `instance.example/hooks/README.md`)
- `recovery.jsonl` — Append-only audit log written by `recover.py` each time a stale In Progress mission is processed at startup

## Python compatibility

All code must support **Python 3.11+**. Do not use syntax or stdlib features introduced after Python 3.11 (e.g., `type` statements from 3.12, `TypeVar` defaults from 3.13). CI tests against multiple Python versions — if it doesn't run on 3.11, it doesn't ship.

## Linting

All Python code must pass **ruff** (`make lint`) before committing. The ruff configuration lives in `pyproject.toml` under `[tool.ruff]`.

- Run `make lint` to check for violations. Fix all errors before pushing.
- Currently enforced rule sets: **PERF** (performance anti-patterns). New rule sets will be added incrementally as existing violations are cleaned up.
- Test files (`koan/tests/*`) are exempt from PERF rules via `per-file-ignores`.
- When adding new code, avoid introducing violations from rule sets not yet enforced project-wide (E, F, W, I, B are good hygiene even though not yet gated in CI).
- Do not disable ruff rules with `# noqa` comments unless there is a clear, documented reason. Prefer fixing the violation.

## Conventions

- Claude always creates **`<prefix>/*` branches** (default `koan/`, configurable via `branch_prefix` in `config.yaml`), never commits to main
- Project config via `projects.yaml` at KOAN_ROOT (primary), with `KOAN_PROJECTS` env var as fallback. Supports per-project overrides for `cli_provider`, `models`, `tools`, and `git_auto_merge`.
- Environment config via `.env` file and `KOAN_*` variables for secrets and system settings. **CLI provider** is configured via `KOAN_CLI_PROVIDER` env var (primary), with fallback to `CLI_PROVIDER` for backward compatibility. The centralized `get_cli_provider_env()` helper in `utils.py` handles this resolution.
- Multi-project support: up to 50 projects, each with isolated memory under `memory/projects/{name}/`
- **Temp files & provider locks** live under a per-uid directory from `utils.koan_tmp_dir()` (`$XDG_RUNTIME_DIR/koan`, else `/tmp/koan-<uid>/`, mode `0700`), overridable via `KOAN_TMP_DIR`. This keeps multiple users running Kōan on the same host from colliding on shared `/tmp` paths (notably the provider invocation lock). The dir is per-_uid_, not per-instance, because provider auth state is per-user. New code that needs a scratch file in `/tmp` MUST pass `dir=koan_tmp_dir()` to `tempfile.*`; agent prompts that write to `/tmp` MUST use a `mktemp` pattern (never a fixed name).
- Tests use temp directories and isolated env vars — no real Telegram calls
- `system-prompt.md` defines the Claude agent's identity, priorities, and autonomous mode rules
- **No inline prompts in Python code** — LLM prompts MUST be extracted to `.md` files. Skill-bound prompts go in `skills/<scope>/<name>/prompts/` and are loaded via `load_skill_prompt()`. Infrastructure prompts used by `koan/app/` modules stay in `koan/system-prompts/` and are loaded via `load_prompt()`. Reusable prompt fragments live in `koan/system-prompts/_partials/` and are included via `{@include partial-name}` directive (resolved at load time by `prompts.py`).
- **System prompts must be generic** — Never reference specific instance details like owner names in system prompts. Use generic terms like "your human" instead of personal names. Prompts are in English; instance-specific personality and language preferences come from `soul.md`.
- **Never leak private skill/agent/project names** — The public repo must contain zero references to private identifiers from any operator's `instance/` tree. This applies to **source code, comments, docstrings, test fixtures, public docs, example configs, AND commit messages** (which `git log` exposes forever).
  - **Forbidden in public artifacts**: private slash-command names (the operator's internal `/<team>-prefix>_<verb>` form), private agent or third-party tool names invoked by handlers, private bot display names (the operator's Telegram/Jira/GitHub bot handle), private JIRA project key prefixes (the all-caps fragment in keys like `<PREFIX>-12345`), private project name strings that identify the operator's customer, and concrete case numbers.
  - **Generic placeholders** to use in tests, examples, and docs: skill `my_fix` / alias `myfix` / scope `my_team`, agent `my-custom-workflow`, bot `@koan-bot` or `@testbot`, JIRA keys `PROJ-NNN` / `FOO-NNN`, project `my-toolkit`.
  - **Mechanism, not enumeration** — When core code needs to recognise a specific custom skill (e.g. for result forwarding), drive the behaviour off SKILL.md frontmatter flags in the `instance/skills/<scope>/<name>/` tree, not off a hardcoded list of names in `koan/app/`. See `koan/app/skills.py::collect_forward_result_markers` for the pattern: opt-in via `forward_result: true` + optional `title_markers:`, resolved dynamically from the registry at runtime.
  - **Pre-commit check** — maintain a private file (gitignored or outside the repo) at `instance/.leak-patterns` listing your operator's private identifiers, one regex alternation per line, then run before staging:
    ```bash
    patterns="$(paste -sd '|' instance/.leak-patterns)"
    git diff main.. | grep '^+' | egrep -i "$patterns"
    ```
    Must return empty. The `^+` filter restricts to lines being added on the current branch, so pre-existing leaks on `main` don't false-positive. Keeping the pattern list outside the public repo prevents this convention bullet from itself becoming a leak.
  - **If you find a pre-existing leak on `main`** while working in adjacent code, scrub it in the same branch — don't leave it as someone else's problem.
- **User manual maintenance** — When adding, removing, or modifying a core skill, update `docs/users/user-manual.md` and `docs/users/skills.md` accordingly: add the skill to the appropriate tier section and the quick-reference appendix. The manual and skills reference must stay in sync with `koan/skills/core/`.
- **Help group enforcement** — Every core skill MUST have a `group:` field in its SKILL.md frontmatter (one of: missions, code, pr, status, config, ideas, system). This ensures commands are discoverable via `/help`. If adding a new hardcoded core command (not skill-based), add it to `_CORE_COMMAND_HELP` in `command_handlers.py`. The test suite enforces this — `TestCoreSkillGroupEnforcement` will fail if a core skill is missing its group. The `integrations` group is reserved for custom skills under `instance/skills/<scope>/` (team-specific integrations) — not for core skills.
- **Custom skills on GitHub/Jira** — Skills under `instance/skills/<scope>/` can be exposed to GitHub and Jira @mentions with a single `github_enabled: true` flag (Jira reuses it; there is no separate `jira_enabled`). Custom skills with a `handler.py` are dispatched **in-process** by `koan/app/external_skill_dispatch.py` — the helper synthesizes a `SkillContext`, auto-feeds the originating Jira key when the author omits one, and calls `execute_skill()` directly. This avoids queueing a `/cmd …` slash mission that has no registered runner. Set `group: integrations` so they render in the dedicated help section.
- **No hyphens in skill names or aliases** — Skill command names, aliases, and directory names MUST use underscores (`_`), never hyphens (`-`). Hyphens break Telegram command parsing because Telegram treats the hyphen as a word boundary, cutting the command short. Example: use `dead_code` not `dead-code`, `scaffold_skill` not `scaffold-skill`.
- **Adding a new core skill** — Every core skill requires ALL of the following. Missing any step leaves the skill broken or undiscoverable:
  1. **Skill directory**: Create `koan/skills/core/<skill_name>/SKILL.md` with frontmatter including `name`, `description`, `group` (one of: missions, code, pr, status, config, ideas, system), `commands`, and `audience`. Add `handler.py` if the skill needs Python logic (omit for prompt-only skills).
  2. **Runner registration** (if the skill runs via the agent loop): Add an entry in `_SKILL_RUNNERS` dict in `skill_dispatch.py` mapping the command name to its runner module. Also add any needed command builder in `_COMMAND_BUILDERS` and validation in `validate_skill_args()`. (Quota-detection handling for skill stdout is already centralized in `mission_executor.py` — see the `skill_dispatch.py` note above; nothing per-runner is required.)
  3. **CLAUDE.md skill list**: Update the "Core skills" line in the Skills system section to include the new skill name (keep alphabetical order).
  4. **User manual and skills reference**: Update `docs/users/user-manual.md` and `docs/users/skills.md` — add the skill to the appropriate tier section and the quick-reference appendix.
  5. **Tests**: The `TestCoreSkillGroupEnforcement` test will fail if the SKILL.md is missing or lacks a `group:` field — run the test suite to verify.
     See `koan/skills/README.md` for the full SKILL.md format and handler conventions.
- **Documentation maintenance** — When adding or modifying a feature, update the corresponding section in `README.md` and/or the relevant docs file. Use the nested docs layout in `docs/README.md`: user behavior in `docs/users/`, daemon design in `docs/architecture/`, providers in `docs/providers/`, messaging and tracker integrations in `docs/messaging/`, operations in `docs/operations/`, durable decisions in `docs/design/`, threat models and audit docs in `docs/security/`, and deployment guides in `docs/setup/`. If no documentation file exists for the feature, create one in the matching directory. Public-facing documentation and implementation references must stay in sync with the codebase — undocumented features are invisible to users.
