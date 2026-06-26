# Skills Reference

> **For a guided introduction**, see the [User Manual](user-manual.md) — organized by skill level with use cases and workflow examples.

Complete reference for all Koan slash commands. Use these via Telegram, Slack, or GitHub @mentions.

> **Extensible:** Drop a `SKILL.md` in `instance/skills/` or install from a Git repo with `/skill install <url>`.
> See [koan/skills/README.md](../../koan/skills/README.md) for the authoring guide.

---

## Mission Management

| Command | Aliases | Description |
|---------|---------|-------------|
| `/mission <text>` | — | Queue a new mission. Use `--now` to prioritize |
| `/list` | `/queue`, `/ls` | List pending and in-progress missions |
| `/priority <n> <pos>` | — | Reorder a pending mission in the queue |
| `/cancel <n or keyword>` | `/remove`, `/clear` | Cancel a pending mission |
| `/abort` | — | Abort the current in-progress mission |
| `/idea <text>` | `/ideas`, `/buffer` | Add to the ideas backlog (promote to mission later) |

## Recurring Missions

| Command | Aliases | Description |
|---------|---------|-------------|
| `/daily <text>` | — | Schedule a daily recurring mission |
| `/hourly <text>` | — | Schedule an hourly recurring mission |
| `/weekly <text>` | — | Schedule a weekly recurring mission |
| `/recurring` | — | List all recurring missions |
| `/recurring resume <n>` | — | Re-enable a disabled recurring mission |
| `/recurring run [n]` | — | Force an immediate run of a recurring mission |
| `/recurring pause <n>` | — | Disable a recurring mission without deleting |
| `/recurring cancel <n>` | — | Remove a recurring mission |
| `/recurring days <n> <days>` | — | Set a day-of-week filter on a recurring mission |

## Code & Project Operations

| Command | Aliases | Description | GitHub @mention |
|---------|---------|-------------|:-:|
| `/plan [--iterations N] <desc>` | — | Deep-think an idea, create a tracker issue with task-level plan (file map, checkbox steps, code blocks, self-review). `--iterations N` (1-5) runs N critique+refine rounds. | — |
| `/deepplan <desc>` | `/deeplan` | Spec-first design: explore approaches, post spec, queue /plan | — |
| `/implement <issue>` | `/impl` | Queue implementation for a GitHub or Jira issue; creates a draft PR, then privately reviews/fixes Important+ findings by default | Yes |
| `/fix <issue>` | — | Diagnose → understand → plan → test → implement → submit PR, then privately reviews/fixes Important+ findings by default. Use `--skip-diagnose` to bypass the diagnostic. A PR URL is redirected to `/rebase` (preserving `--now` and trailing context) | Yes |
| `/debug <issue>` | `/dbg` | Structured 4-step debug loop: reproduce → hypothesize → minimal fix → verify. Auto-queued when `/fix` fails (opt-in via `debug_escalation.on_fix_failure` in config.yaml) | Yes |
| `/review <PR> [PR ...] [--bot-comments]` | `/rv` | Review one or more pull requests; each URL queues a separate review mission. `--bot-comments` triages bot findings | Yes |
| `/ultrareview <PR>` | `/urv` | Ultra-thorough review: architecture + silent-failure passes combined | Yes |
| `/explain <PR>` | `/xp` | Explain a PR's changes in plain language with examples and alternative approaches | Yes |
| `/rebase <PR> [focus area]` | `/rb` | Rebase a PR onto its base branch; trailing text after the URL is threaded into the mission as focus context | Yes |
| `/squash <PR>` | `/sq` | Squash all PR commits into one clean commit | Yes |
| `/recreate <PR>` | `/rc` | Re-implement a PR from scratch on a fresh branch | Yes |
| `/refactor <desc>` | `/rf` | Targeted refactoring mission | Yes |
| `/check <url>` | `/inspect` | Run project health checks on a PR or issue (rebase, review, plan) | — |
| `/check_need <url>` | `/need`, `/needs` | Analyze if a PR or issue is still needed vs. current main | — |
| `/ci_check <PR>\|--enable\|--disable` | — | Check and fix CI failures on a PR; toggle CI system | — |
| `/pr <PR>` | — | Review and update a GitHub pull request | — |
| `/claudemd [project]` | `/claude`, `/claude.md` | Refresh or create a project's CLAUDE.md | — |
| `/doc <project> [cats]` | `/docs` | Extract structured documentation to docs/ | Yes |
| `/profile <project>` | `/perf`, `/benchmark` | Performance profiling mission | Yes |

For URL-based `/plan`, `/deepplan`, `/implement`, and `/fix`, append `branch:<name>` to
override the base branch for that mission.

The private post-PR review gate for `/fix`, `/implement`, and `/rebase` is
backend-only: it reuses `/review` analysis, fixes Blocking/Important findings
on the same branch, and does not post review comments or verdicts. It is
opt-in (disabled by default during the testing phase) — enable and configure it
with `private_review_gate` in `config.yaml` or `projects.yaml`.

`/review` (and the private gate) inject the project's filtered learnings and
human-curated context/priorities into the review prompt, ranked against the PR
content. Enable `review_memory` in `config.yaml` to also include recent typed
project memory (decisions, observations) from the SQLite memory index.

**Inline comments (opt-in):** Set `review_inline_comments.enabled: true` in
`config.yaml` to also post each finding as an inline PR comment anchored to its
code location, in addition to the bucketed summary comment (which is unchanged).
Each inline thread shows the same severity marker (🔴/🟡/🟢) and the full finding
detail, so reviewers can react or resolve in place. Cap the volume with
`review_inline_comments.max_comments` (default 25). Re-running `/review` is
idempotent (already-anchored findings are skipped); multi-line findings anchor
to their full range; if all posts fail, you are notified. Disabled by default.

Skills marked **GitHub @mention** can be triggered by commenting `@koan-bot <command>` on a PR or issue. See [GitHub commands](../messaging/github-commands.md).

## PR Management

| Command | Aliases | Description | GitHub @mention |
|---------|---------|-------------|:-:|
| `/ask <comment-url>` | — | Ask a question about a PR/issue — posts AI reply to GitHub | Yes |
| `/reviewrebase <PR>` | `/rr` | Review then rebase a PR (combo: /review → /rebase) | Yes |
| `/planimplement <issue>` | `/planimp`, `/planimpl`, `/planit`, `/plandoit` | Plan then implement an issue (combo: /plan → /implement) | Yes |
| `/branches [project]` | `/br`, `/prs` | List koan branches + open PRs with merge order | — |
| `/orphans <project>` | `/orphan` | Recover orphan branches — rebase onto main + draft PR | — |
| `/done [project]` | `/merged` | List PRs merged in the last 24 hours | — |
| `/diagnose [project]` | `/dx` | Find the last failed mission and queue a fix attempt | — |
| `/gh_request <url> <text>` | — | Route a natural-language GitHub request to the right skill | Yes |

## Exploration & Analysis

| Command | Aliases | Description | GitHub @mention |
|---------|---------|-------------|:-:|
| `/brainstorm <topic>` | — | Decompose topic into linked sub-issues + master tracking issue | Yes |
| `/ai <topic>` | `/ia` | Queue an AI exploration mission (deep, with codebase access) | — |
| `/deep [project] [focus]` | — | Thorough autonomous exploration with full tool access | — |
| `/magic <topic>` | — | Instant creative exploration (quick, no mission queue) | — |
| `/sparring` | — | Strategic challenge session — thinking, not code | — |
| `/audit <project>` | — | Audit project, create tracker issues for each finding (top 5) | Yes |
| `/security_audit <project>` | `/security`, `/secu` | Security audit, find critical vulnerabilities (top 5) | Yes |
| `/private_security_audit <project>` | `/private_security`, `/psecu` | Security audit, findings to journal only (no GitHub) | — |
| `/tech_debt [project]` | `/td`, `/debt` | Scan for duplicated code, complex functions, testing gaps | — |
| `/dead_code [project]` | `/dc` | Scan for unused imports, functions, classes, dead branches | — |
| `/spec_audit [project]` | `/sa`, `/drift` | Audit docs/code alignment, queue fix missions | — |
| `/gha_audit [project]` | `/gha` | Scan GitHub Actions workflows for security vulnerabilities | — |
| `/audit_all [project]` | `/aa` | Run security_audit, dead_code, and profile in parallel | — |
| `/changelog [project]` | `/changes` | Generate changelog from recent commits and journal entries | — |
| `/stats [project]` | — | Show session outcome statistics per project | — |

## Communication & Reflection

| Command | Aliases | Description |
|---------|---------|-------------|
| `/chat <msg>` | — | Force chat mode (bypass mission detection) |
| `/reflect <msg>` | `/think` | Write a reflection to the shared journal |
| `/journal [project] [date]` | `/log` | View journal entries |
| `/email` | — | Email status digest (use `/email test` to verify setup) |

## Status & Monitoring

| Command | Aliases | Description |
|---------|---------|-------------|
| `/status` | `/st` | Show agent state, missions, and loop health |
| `/brief` | `/digest` | Daily digest — pending, completions, quota, journal highlights |
| `/ping` | — | Check if the agent loop is alive |
| `/live` | `/progress` | Show live progress from the current run |
| `/logs [run\|awake\|all]` | — | Show last 20 lines from logs (default: run) |
| `/quota [N]` | `/q` | Check LLM quota (live), or override remaining % |
| `/usage` | — | Detailed quota and progress |
| `/metrics` | — | Mission success rates and reliability stats |
| `/report [--week\|--month]` | `/weekly_report`, `/monthly_report` | PR activity report (created, merged %, interacted) per-project + global; defaults to both weekly and monthly |
| `/doctor` | — | Diagnostic self-checks; `--fix` auto-repairs, `--full` adds connectivity |
| `/models` | `/model` | Show resolved model config for the active CLI provider |
| `/config_check` | `/cfgcheck`, `/configcheck` | Detect drift between instance/config.yaml and the template |
| `/check_notifications` | `/read` | Force immediate GitHub + Jira notification check |
| `/inbox` | — | Force GitHub notification check + show queued mail count (works while paused) |
| `/rescan` | `/rescan_heads` | Re-check all projects for remote HEAD branch changes |
| `/gh` | — | Show GitHub CLI auth status and connected user |
| `/time` | `/date` | Show current server date and time |
| `/version` | `/ver`, `/v` | Show Kōan version (tag, commit, commits ahead) |
| `/verbose` | — | Enable real-time progress updates |
| `/silent` | — | Disable real-time progress updates |
| `/messaging_level` | `/msglevel` | Show or set bridge verbosity (`debug` / `normal`) |

## Configuration

| Command | Aliases | Description |
|---------|---------|-------------|
| `/projects` | `/proj` | List configured projects |
| `/tracker` | — | Show or set per-project issue tracker routing |
| `/alias <proj> <short>` | — | Create project shortcut (e.g. `/alias Template2 tt`) |
| `/unalias <short>` | — | Remove a project alias |
| `/focus [duration]` | — | Lock the agent to one project (suppress exploration) |
| `/unfocus` | — | Exit focus mode |
| `/passive [duration]` | — | Enter read-only passive mode |
| `/active` | — | Exit passive mode, resume execution |
| `/explore [project\|all\|none]` | `/exploration`, `/noexplore [project\|all]` | Toggle per-project exploration mode; `all`/`none` also sets default for future projects |
| `/autoreview [project]` | `/auto_review`, `/noautoreview` | Toggle automatic review+rebase after PR creation per project |
| `/language <lang>` | `/lng`, `/fr`, `/en` | Set reply language preference |

## System

| Command | Aliases | Description |
|---------|---------|-------------|
| `/pause` | `/sleep` | Pause mission processing |
| `/resume` | `/work`, `/awake`, `/run`, `/start` | Resume mission processing |
| `/shutdown` | — | Shutdown both agent loop and messaging bridge |
| `/update` | `/upgrade` | Update to latest commit on main, restart |
| `/update_last_release` | — | Update to most recent release tag, restart |
| `/reset` | — | Reset run counter to 0 (resumes if paused by max_runs) |
| `/restart` | — | Restart processes (no code pull) |
| `/snapshot` | — | Export memory state to a portable file |

## Project Management

| Command | Aliases | Description |
|---------|---------|-------------|
| `/add_project <url>` | — | Clone a GitHub repo and add it to the workspace |
| `/delete_project <name>` | `/delete`, `/del` | Remove a project from workspace |
| `/rename <old> <new>` | `/rename_project` | Rename a project everywhere (config, memory, journals) |

## Power Tools

| Command | Aliases | Description |
|---------|---------|-------------|
| `/incident <error>` | — | Triage a production error from a stack trace or log snippet |
| `/scaffold_skill <scope> <name> <desc>` | `/scaffold`, `/new_skill` | Generate SKILL.md + handler.py for a new custom skill |
| `/rtk [setup\|uninstall\|gain\|on\|off]` | — | Manage optional rtk integration for compressed tool output |
| `/ideas` | — | List all ideas in the backlog |

---

## Skill Types

- **Instant** (`worker: false`) — Executes immediately, returns a response. Examples: `/status`, `/list`, `/gha_audit`.
- **Worker** (`worker: true`) — Runs in a background thread (Claude calls, API requests). Examples: `/magic`, `/chat`, `/sparring`.
- **Hybrid** (`audience: hybrid`) — Available from both Telegram/Slack and as agent-dispatched skills. Examples: `/plan`, `/implement`, `/review`.

## Custom Skills

Install skills from Git repos:

```
/skill install https://github.com/your-org/koan-skills.git
/skill approve <scope> <fingerprint>
/skill update <scope>
/skill remove <scope>
```

New installs and `/scaffold_skill` output are **quarantined** behind an
approval gate — the registry will not load them until `/skill approve` is run
with the fingerprint shown in the install reply. Inspect the cloned files
before approving. Set `skills.allowed_hosts` in `config.yaml` to restrict
which Git hosts `/skill install` can fetch from.

Or create your own in `instance/skills/<scope>/<name>/` with a `SKILL.md` file. See [koan/skills/README.md](../../koan/skills/README.md) for the full authoring guide.
