# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Kōan

Kōan is an autonomous background agent that uses idle Claude API quota to work on local projects. It runs as a continuous loop, pulling missions from a shared file, executing them via Claude Code CLI, and communicating progress via Telegram. Philosophy: "The agent proposes. The human decides." — no unsupervised code modifications.

## On-demand guidance (nested CLAUDE.md)

This root file is intentionally small. Detailed, scope-specific guidance lives in nested `CLAUDE.md` files that Claude Code auto-loads when you work in that subtree:

- **`koan/CLAUDE.md`** — Python rules: test suite, Python 3.11+ compatibility, ruff linting, temp-file & prompt-extraction conventions. Loads for all package code.
- **`koan/app/CLAUDE.md`** — Architecture overview, the full per-module reference (`Key modules`), and the `instance/` runtime-state layout. Loads when editing `koan/app/`.
- **`koan/skills/CLAUDE.md`** — Skills system, authoring conventions, and the "adding a new core skill" checklist. Loads when editing `koan/skills/`.

This content previously lived inline here (≈40k chars); it was split out to keep the always-loaded context small. All prior content is preserved verbatim in the nested files.

## Specs discipline (mandatory)

`specs/` is the **single source of truth for design** — *why* a component exists, the
contract it upholds, and what breaks if you change it. Specs drive the application; docs
explain how to use it (see `specs/README.md` for the specs-vs-docs split). This discipline
is **not optional**:

1. **Before implementing** any feature or refactor, READ the relevant spec first — via
   `/brain ask` rather than blind grep, see "Documentation first" below:
   - Component change → `specs/components/<group>.md` (core, agent-loop, bridge,
     providers, git-github, issue-tracking, skills, web).
   - Skill change → `specs/skills/<skill-name>.md`.
   The spec tells you the invariants you must not silently break. Do not skip this because
   a change "looks small" — small changes break contracts too.
2. **After implementing**, UPDATE the spec in the same branch to reflect the new design:
   new types/functions, changed integration points, resolved or newly-introduced debt. A
   PR that alters a component's contract without updating its spec is **incomplete**.
3. **No spec yet?** If you touch a component or skill that has no spec, WRITE one using
   `specs/components/` conventions or `specs/skills/SKILL_SPEC_TEMPLATE.md`. Phase 1 ships
   specs for the highest-impact pieces; the rest are added on-demand as they are touched.
4. **`specs/<NNN-slug>/` (speckit) is a different, ephemeral population** — not the durable
   contracts this discipline governs. See `specs/README.md`'s "components/, skills/ vs.
   `<NNN-feature-slug>/`" section. When a speckit feature ships, step 2 above (updating the
   matching `specs/components/<group>.md`) is what makes it durable — the speckit folder
   itself stays as planning history.

Specs and `docs/` coexist — most non-trivial changes update both. Use specs to anchor
clean refactoring: change the spec's contract deliberately, then make the code match.

## Documentation first

`docs/` and `specs/` are each independent OKF v0.1 knowledge bundles (see
`docs/SPEC.md` for the normative, bundle-agnostic spec; `docs/SCHEMA.md` and
`specs/SCHEMA.md` for the per-bundle conventions built on top of it), also jointly
indexed via the `wiki/` directory (`wiki/index.md`, `wiki/docs`, `wiki/specs-components`,
`wiki/specs-skills` symlinks — see `wiki/SCHEMA.md`). The **`/brain` skill**
(`.claude/skills/brain/`) is the entrypoint for consulting and extending both bundles —
it is fully self-sufficient (acquisition, index-first navigation, and OKF-conformance
glue), with no dependency on the `llm-wiki` plugin's `/wiki:*` commands (see
`wiki/SCHEMA.md` for why). Treat this as a hard rule on every feature, refactor, or
architecture decision: **read it first (Consult), update it last (Capture)**.

- **Consult — before you plan, research, or refactor.** Run `/brain ask "<question>"`
  (or `/brain search`, an alias). It reads `wiki/index.md` first (never greps `docs/`/
  `specs/` blindly), picks candidate pages from their one-line summaries, opens only
  those (+ backlinks), and cites them by path. If the index doesn't clearly point to a
  page, that's the signal coverage is missing — say so plainly rather than guessing.
  State explicitly what you found (naming the docs) — or that nothing relevant existed
  — before proposing changes. Never rely on source-code inspection alone when a
  relevant doc exists. **In Plan Mode**, the plan file's Context section must state
  what `docs/`/`specs/` said (or that nothing relevant was found) before the
  recommended approach.

  `/brain lint` (wrapping the broadened `scripts/wiki_check.py`) is a periodic health
  check, not part of the per-task loop.

- **Source of truth order.** (1) current source code, (2) `docs/`/`specs/`, (3) inline
  code comments, (4) assumptions. If docs conflict with the code, trust the code and
  propose a docs update in the same change — never silently pick a side.

- **Capture — write/update after you implement.** After changing user behavior,
  configuration, daemon flow, provider behavior, shared state, safety boundaries, or an
  important implementation decision, create or update the relevant page under
  `docs/architecture/`, `docs/users/`, `docs/providers/`, `docs/messaging/`,
  `docs/operations/`, `docs/design/`, `docs/security/`, or `docs/setup/` (see
  `docs/SCHEMA.md` for frontmatter/tagging conventions), and the matching
  `specs/components/<group>.md` if the change touches a component's contract (see
  "Specs discipline" above). Then run **`/brain sync`** to close the loop: it bumps
  `updated:` frontmatter (and adds `description:` on new/touched pages only — existing
  pages are not bulk-backfilled), regenerates any stale `index.md` via
  `scripts/okf_backfill.py indexes`, and refreshes the page's `wiki/index.md` entry.
  For core skill changes, also update `docs/users/user-manual.md` and
  `docs/users/skills.md`.

- **Acquire — capture external material.** When a web article, an existing document,
  or a stray idea worth preserving comes up mid-task (not tied to a feature you just
  built), run **`/brain ingest <path|url|"idea sentence">`** — it snapshots the source
  into the git-ignored `raw/` directory and compiles a durable, cited
  `docs/reference/*.md` page. This is separate from feature docs: it's for knowledge
  that doesn't originate from code you wrote.

- **Wiki bookkeeping is exempt from the "no unsupervised modification" principle
  below** — frontmatter fields, `wiki/index.md`/`docs/index.md`/`specs/index.md`/
  per-folder `index.md` entries, and `wiki/log.md` lines are committed directly as part
  of the same change/PR, no separate review step for that part specifically. This does
  not extend to actual spec/contract, doc-body, or code changes. A CI job
  (`.github/workflows/wiki-sync.yml`) backstops anything a session missed by pushing a
  same-branch fix commit — never to `main`, never a separate PR.

- **`.claude/skills/` vs. `koan/skills/core/`.** `brain` (like the `speckit-*` skills)
  is a Claude-Code-native project skill under `.claude/skills/`, invoked directly in a
  session — it is not a `koan/skills/core/` runtime skill dispatched via Telegram/
  GitHub/Jira, has no attachment to `app/skill_evals.py`'s eval harness, and is out of
  scope for `koan/skills/CLAUDE.md`'s "adding a new core skill" checklist. Never
  conflate the two.

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

## Architecture

Two parallel processes run independently: an **`awake.py`** Telegram bridge (polls Telegram, classifies chat vs. mission, flushes the outbox) and a **`run.py`** agent loop (pure-Python main loop that invokes the Claude CLI per mission and runs the lifecycle state machine). They communicate through shared files in `instance/` via atomic writes (`utils.atomic_write()`); exclusive process instances are enforced by `pid_manager.py` (PID file + `fcntl.flock()`).

The full two-process detail, the complete per-module reference (`Key modules`), and the `instance/` runtime-state file layout live in **`koan/app/CLAUDE.md`** (auto-loaded when editing `koan/app/`).

## Conventions

- Claude always creates **`<prefix>/*` branches** (default `koan/`, configurable via `branch_prefix` in `config.yaml`), never commits to main
- Project config via `projects.yaml` at KOAN_ROOT (primary), with `KOAN_PROJECTS` env var as fallback. Supports per-project overrides for `cli_provider`, `models`, `tools`, and `git_auto_merge`.
- Environment config via `.env` file and `KOAN_*` variables for secrets and system settings. **CLI provider** is configured via `KOAN_CLI_PROVIDER` env var (primary), with fallback to `CLI_PROVIDER` for backward compatibility. The centralized `get_cli_provider_env()` helper in `utils.py` handles this resolution.
- Multi-project support: up to 50 projects, each with isolated memory under `memory/projects/{name}/`
- **`KOAN.md` convention** — An optional project-root `KOAN.md` (same format as `CLAUDE.md`) is injected into the autonomous agent's system prompt but is **not** loaded by interactive Claude Code sessions. Use it for koan-only, per-project guidance. Precedence: mission instruction > `KOAN.md` > `CLAUDE.md`/defaults. See `docs/users/koan-md.md`.
- `system-prompt.md` defines the Claude agent's identity, priorities, and autonomous mode rules
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
- **User manual maintenance** — When adding, removing, or modifying a core skill, update `docs/users/user-manual.md` and `docs/users/skills.md` accordingly: add the skill to the appropriate tier section and the quick-reference appendix. The manual and skills reference must stay in sync with `koan/skills/core/`. (Skill authoring details and the full new-skill checklist live in `koan/skills/CLAUDE.md`.)
- **Documentation maintenance** — When adding or modifying a feature, update the corresponding section in `README.md` and/or the relevant docs file. Use the nested docs layout in `docs/README.md`: user behavior in `docs/users/`, daemon design in `docs/architecture/`, providers in `docs/providers/`, messaging and tracker integrations in `docs/messaging/`, operations in `docs/operations/`, durable decisions in `docs/design/`, threat models and audit docs in `docs/security/`, and deployment guides in `docs/setup/`. If no documentation file exists for the feature, create one in the matching directory. Public-facing documentation and implementation references must stay in sync with the codebase — undocumented features are invisible to users.

> Python-specific conventions (temp files, linting, tests, prompt extraction) and skill-authoring conventions (help groups, naming, the new-skill checklist) live in `koan/CLAUDE.md` and `koan/skills/CLAUDE.md` respectively — see "On-demand guidance" above.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
specs/001-speckit-native-support/plan.md
<!-- SPECKIT END -->
