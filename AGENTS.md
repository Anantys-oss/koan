# AGENTS.md

This file provides guidance to Codex and other coding agents working in this
repository. It is adapted from `CLAUDE.md`; keep both files aligned when project
norms change.

For deeper historical context, Claude-provider details, or guidance not covered
here, consult `CLAUDE.md`. For Codex behavior, `AGENTS.md` is authoritative when
the two files differ.

## Project Overview

Koan is an autonomous background agent that uses idle CLI/API quota to work on
local projects. It runs as a continuous loop, pulls missions from shared files,
executes them through configured CLI providers, and communicates progress via
Telegram.

Core philosophy: "The agent proposes. The human decides." Do not introduce
unsupervised code modification behavior unless explicitly requested.

When existing docs or code mention Claude, treat that as Koan's Claude provider
or legacy Claude Code workflow unless the task explicitly asks to use Claude
Code.

## Documentation First

- Before planning or implementing a feature or important refactor, inspect the
  relevant documentation with `grep`, `find`, or equivalent search. Start at
  `docs/README.md`, then read the matching pages under `docs/architecture/`,
  `docs/users/`, `docs/providers/`, `docs/messaging/`, or `docs/operations/`.
- Treat docs as context to verify against code, not as unquestioned truth. If
  code and docs disagree, preserve current code behavior unless the task says
  otherwise, and update the docs to match the resulting behavior.
- After changing user behavior, configuration, daemon flow, provider behavior,
  shared state, safety boundaries, or an important implementation decision,
  update the relevant docs in the same branch.
- For core skill changes, update both `docs/users/user-manual.md` and
  `docs/users/skills.md`.

## Commands

```bash
make setup          # Create venv, install dependencies
make start          # Start full stack
make stop           # Stop all running processes
make status         # Show running process status
make logs           # Watch live output
make run            # Start main agent loop
make awake          # Start Telegram bridge
make ollama         # Start full Ollama stack
make dashboard      # Start Flask web dashboard on port 5001
make lint           # Run ruff linter
make test           # Run full test suite
make coverage       # Run tests with detailed coverage report
make say m="..."    # Send test message as if from Telegram
make rename-project old=X new=Y [apply=1]  # Rename a project
make clean          # Remove venv
```

Run a single test file with `KOAN_ROOT` set:

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_missions.py -v
```

## Testing Rules

- `KOAN_ROOT` must be set when running tests. Many modules check it at import
  time and may raise `SystemExit` if it is missing.
- Use a temporary root such as `KOAN_ROOT=/tmp/test-koan`.
- Never call real Claude, Telegram, or external provider subprocesses in tests.
  Mock the relevant boundary.
- Mock `format_and_send` where code would invoke Claude CLI for message
  formatting.
- With `runpy.run_module()` CLI tests, patch both
  `app.<module>.format_and_send` and `app.notify.format_and_send`; `runpy`
  re-executes the module and can bypass the first import-level binding.
- When `load_dotenv()` would reload env vars from `.env`, patch
  `app.notify.load_dotenv` too.
- Test behavior, not implementation text. Assert on outputs, side effects,
  raised exceptions, file contents, or other observable behavior.
- For `run_gh()` and `api()` error handling tests, mock at the `run_gh` or
  `api` level. Do not mock `app.github.subprocess.run`, because that triggers
  real retry backoff sleeps.

## Architecture

Two long-running processes operate independently:

- `awake.py`: Telegram bridge. Polls Telegram, classifies chat vs mission
  messages, queues missions, and flushes `outbox.md` replies.
- `run.py`: Agent loop. Picks pending missions, transitions lifecycle state,
  executes missions through the configured provider, tracks usage, handles
  quota, and writes status.

Processes communicate through shared files in `instance/` using atomic writes
and file locks. Exclusive process instances are enforced by PID files and
`fcntl.flock()`.

### Core Data And Config

- `koan/app/missions.py`: source of truth for `missions.md` parsing and mission
  lifecycle transitions.
- `koan/app/projects_config.py`: loads `projects.yaml` and merges defaults with
  per-project overrides.
- `koan/app/projects_migration.py`: migrates legacy env-var project config to
  `projects.yaml`.
- `koan/app/utils.py`: file locking, config loading, atomic writes, branch
  prefixes, and known-project discovery.
- `koan/app/config.py`: centralized config loading and provider/model/tool
  selection helpers.
- `koan/app/constants.py`: shared numeric constants for the agent loop.
- `koan/app/run_log.py`: colored logging wrapper.
- `koan/app/commit_conventions.py`: commit convention detection and
  `COMMIT_SUBJECT:` parsing.

### Agent Loop Pipeline

- `iteration_manager.py`: per-iteration decisions, usage refresh, mode
  selection, recurring injection, mission picking, project resolution.
- `mission_runner.py`: mission lifecycle execution, JSON output parsing, usage
  tracking, archival, reflection, auto-merge.
- `loop_manager.py`: focus resolution, pending file creation, interruptible
  sleep, project validation.
- `contemplative_runner.py`: reflection session runner.
- `quota_handler.py`: quota exhaustion detection and pause-state creation.
- `prompt_builder.py`: agent prompt assembly and budget-aware context trimming.
- `event_scheduler.py`: one-shot scheduled mission triggers.
- `suggestion_engine.py`: recurring/schedule recommendation generation.
- `pr_review_learning.py`: extracts lessons from PR reviews and dispatches
  unresolved review comments.
- `skill_dispatch.py`: direct `/command` mission dispatch without an LLM agent.
- `stagnation_monitor.py`: kills stuck subprocess groups and requeues missions.
- `hooks.py`: lifecycle hook discovery and execution from `instance/hooks/`.

### Bridge And Process Management

- `awake.py`: main bridge loop.
- `command_handlers.py`: Telegram command handling.
- `bridge_state.py`: shared bridge state.
- `bridge_log.py`: bridge logging.
- `notify.py`: Telegram notification helper with flood protection.
- `pid_manager.py`: process startup, shutdown, and PID locking.
- `pause_manager.py`: pause state management.
- `restart_manager.py`: restart signaling.
- `focus_manager.py`: focus mode.
- `passive_manager.py`: passive read-only mode.

### Providers

Provider code lives under `koan/app/provider/`.

- `provider/base.py`: provider base class and tool constants.
- `provider/claude.py`: Claude Code CLI provider.
- `provider/copilot.py`: GitHub Copilot CLI provider.
- `provider/__init__.py`: provider registry, resolution, cached singleton, and
  convenience functions.
- `cli_provider.py`: legacy facade; prefer importing from `provider` directly
  for new code.

### Git And GitHub

- `git_sync.py` / `git_auto_merge.py`: branch tracking and configurable
  auto-merge.
- `github.py`: centralized `gh` CLI wrapper.
- `github_url_parser.py`: GitHub URL parsing.
- `github_skill_helpers.py`: helpers for GitHub-related skills.
- `github_config.py`: GitHub notification config.
- `github_notifications.py`: notification fetching and deduplication.
- `github_command_handler.py`: converts authorized GitHub mentions to missions.
- `rebase_pr.py`: PR rebase workflow.
- `recreate_pr.py`: PR recreation workflow.
- `claude_step.py`: shared git operations and Claude CLI invocation helpers.
- `remote_rename_detector.py`: detects and fixes renamed remotes.
- `head_tracker.py`: tracks remote default-branch changes.

### Other Important Modules

- `memory_manager.py`: per-project memory isolation, compaction, cleanup.
- `usage_tracker.py`: quota parsing and autonomous mode affordability.
- `burn_rate.py`: rolling quota burn-rate estimator.
- `recover.py`: crash recovery for stale in-progress missions.
- `prompts.py`: system prompt loader with `{@include partial-name}` support.
- `skill_manager.py`: external skill package manager.
- `claudemd_refresh.py`: `CLAUDE.md` refresh pipeline.
- `update_manager.py`: self-update support.
- `auto_update.py`: automatic update checker.
- `ci_dispatch.py`: dispatches CI-fix missions for Koan-authored PRs.
- `security_review.py`: differential security review on mission diffs.
- `rename_project.py`: project rename CLI.

## Skills System

Skills live under `koan/skills/`. Each skill has `SKILL.md` frontmatter and may
include a `handler.py`.

- `koan/app/skills.py` discovers skills, parses frontmatter, maps commands and
  aliases, and dispatches execution.
- Core skills live in `koan/skills/core/`.
- Custom skills load from `instance/skills/<scope>/`.
- Handler signature: `def handle(ctx: SkillContext) -> Optional[str]`.
- `worker: true` marks blocking skills that run in a background thread.
- `github_enabled: true` exposes skills to GitHub and Jira mentions.
- `github_context_aware: true` means the skill accepts additional context after
  the command.
- Combo skills use `sub_commands` frontmatter.
- Prompt-only skills omit `handler.py` and use prompt text after frontmatter.
- See `koan/skills/README.md` before adding or changing skills.

When adding a new core skill, do all of the following:

1. Create `koan/skills/core/<skill_name>/SKILL.md` with `name`, `description`,
   `group`, `commands`, and `audience`.
2. Add `handler.py` if the skill needs Python logic.
3. If the skill runs through the agent loop, register it in `_SKILL_RUNNERS` in
   `skill_dispatch.py`, and add any needed command builder or validation.
4. Update the core skills list in `CLAUDE.md`.
5. Update `docs/users/user-manual.md` and `docs/users/skills.md`.
6. Run the relevant tests, including core skill group enforcement.

Skill names, aliases, and directories must use underscores, not hyphens.

## Instance Directory

`instance/` is gitignored and can be copied from `instance.example/`. It holds:

- `missions.md`: task queue.
- `outbox.md`: bot-to-Telegram message queue.
- `config.yaml`: per-instance configuration.
- `soul.md`: agent personality definition.
- `memory/`: global and per-project memory.
- `journal/`: daily logs.
- `events/`: scheduled mission JSON files.
- `hooks/`: user-defined lifecycle hooks.

## Python And Linting

- Support Python 3.11+.
- Do not use syntax or stdlib features introduced after Python 3.11.
- All Python code must pass `make lint` before committing.
- Ruff configuration lives in `pyproject.toml`.
- Currently enforced rules include PERF; test files are exempt from PERF via
  per-file ignores.
- Avoid introducing violations from common hygiene rule sets even if they are
  not yet CI-gated.
- Do not add `# noqa` unless there is a clear, documented reason.

## Project Conventions

- Agents should create `<prefix>/*` branches, defaulting to `koan/`, and should
  not commit directly to main.
- Project config comes from `projects.yaml` at `KOAN_ROOT`, with
  `KOAN_PROJECTS` as fallback.
- Environment config comes from `.env` and `KOAN_*` variables.
- CLI provider config uses `KOAN_CLI_PROVIDER`, with `CLI_PROVIDER` fallback.
- Multi-project support allows up to 50 projects, each with isolated memory.
- Tests use temp directories and isolated environment variables.
- `system-prompt.md` defines the agent identity, priorities, and autonomous
  mode rules.
- LLM prompts must live in `.md` files, not inline Python strings.
- System prompts must be generic and must not reference private instance
  details such as owner names.
- When adding, removing, or changing a core skill, update
  `docs/users/user-manual.md` and `docs/users/skills.md`.
- Every core skill must have a `group:` field in `SKILL.md`; allowed groups are
  `missions`, `code`, `pr`, `status`, `config`, `ideas`, and `system`.
- Custom integration skills should use `group: integrations`.
- GitHub and Jira exposure for skills uses `github_enabled: true`; there is no
  separate `jira_enabled`.

## Privacy And Public Artifacts

The public repo must not contain private identifiers from any operator's
`instance/` tree. This applies to source code, comments, docstrings, tests,
fixtures, public docs, example configs, and commit messages.

Do not leak:

- Private slash-command names.
- Private agent or third-party tool names.
- Private bot display names.
- Private Jira project key prefixes.
- Private customer or project names.
- Concrete private case numbers.

Use placeholders in tests, examples, and docs:

- Skill: `my_fix`
- Alias: `myfix`
- Scope: `my_team`
- Agent: `my-custom-workflow`
- Bot: `@koan-bot` or `@testbot`
- Jira keys: `PROJ-NNN` or `FOO-NNN`
- Project: `my-toolkit`

Drive custom behavior from skill frontmatter flags instead of hardcoded private
names in `koan/app/`.

Before staging, if a private leak pattern file exists, check added lines:

```bash
patterns="$(paste -sd '|' instance/.leak-patterns)"
git diff main.. | grep '^+' | egrep -i "$patterns"
```

The command should return no matches. Keep the pattern file gitignored or
outside the public repo.

If you find a pre-existing leak on `main` while working in adjacent code, scrub
it in the same branch.

## Documentation Maintenance

When adding or modifying a feature, update the corresponding section in
`README.md` or the relevant file under `docs/`. Use the nested docs layout
described in `docs/README.md`: user-facing behavior belongs under `docs/users/`,
daemon design under `docs/architecture/`, provider setup under
`docs/providers/`, messaging and tracker integrations under `docs/messaging/`,
operations under `docs/operations/`, and durable decisions under `docs/design/`.
If no documentation file exists for the feature, create one in the matching
directory. Public-facing documentation and implementation references must stay
in sync with code behavior.
