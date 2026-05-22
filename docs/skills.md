# Skills Reference

> **For a guided introduction**, see the [User Manual](user-manual.md) — organized by skill level with use cases and workflow examples.

Complete reference for all Koan slash commands. Use these via Telegram, Slack, or GitHub @mentions.

> **Extensible:** Drop a `SKILL.md` in `instance/skills/` or install from a Git repo with `/skill install <url>`.
> See [koan/skills/README.md](../koan/skills/README.md) for the authoring guide.

---

## Mission Management

| Command | Aliases | Description |
|---------|---------|-------------|
| `/mission <text>` | — | Queue a new mission. Use `--now` to prioritize |
| `/list` | `/queue`, `/ls` | List pending and in-progress missions |
| `/priority <n> <pos>` | `/prio` | Reorder a pending mission in the queue |
| `/cancel <n or keyword>` | `/remove`, `/clear`, `/rm` | Cancel a pending mission |
| `/abort` | — | Abort the current in-progress mission |
| `/live` | `/progress` | Show live progress from the current run |
| `/chat <msg>` | — | Force chat mode (bypass mission detection) |

## Recurring Missions

| Command | Aliases | Description |
|---------|---------|-------------|
| `/daily <text>` | — | Schedule a daily recurring mission |
| `/hourly <text>` | — | Schedule an hourly recurring mission |
| `/weekly <text>` | — | Schedule a weekly recurring mission |
| `/every <interval> <text>` | — | Schedule a mission with custom interval |
| `/recurring` | — | List all recurring missions |
| `/cancel_recurring <n>` | — | Remove a recurring mission |
| `/pause_recurring <n>` | — | Pause a recurring mission |
| `/resume_recurring <n>` | — | Resume a paused recurring mission |
| `/days_recurring <n> <days>` | — | Set specific days for a recurring mission |

## Code & Project Operations

| Command | Aliases | Description | GitHub @mention |
|---------|---------|-------------|:-:|
| `/brainstorm <topic>` | — | Decompose topic into linked sub-issues + master issue | Yes |
| `/plan <desc>` | — | Deep-think an idea, create a GitHub issue with structured plan | Yes |
| `/deepplan <idea>` | `/deeplan` | Spec-first design with Socratic exploration | Yes |
| `/implement <issue>` | `/impl` | Queue implementation for a GitHub issue | Yes |
| `/fix <issue>` | — | Understand → plan → test → implement → submit PR | Yes |
| `/review <PR>` | `/rv` | Review a pull request (supports `--architecture`) | Yes |
| `/refactor <desc>` | `/rf` | Targeted refactoring mission | Yes |
| `/checkup` | `/checkprs` | Health check on all open PRs across projects | — |
| `/check <url>` | `/inspect` | Run project health checks on a PR/issue | — |
| `/ci_check <PR>` | — | Check and fix CI failures on a PR | — |
| `/claudemd [project]` | `/claude`, `/claude.md`, `/claude_md` | Refresh or create a project's CLAUDE.md | — |

Skills marked **GitHub @mention** can be triggered by commenting `@koan-bot <command>` on a PR or issue. See [github-commands.md](github-commands.md).

## PR Management

| Command | Aliases | Description | GitHub @mention |
|---------|---------|-------------|:-:|
| `/ask <comment-url>` | — | Ask a question about a PR/issue — posts AI reply to GitHub | Yes |
| `/pr <PR>` | — | Review and update a GitHub pull request | — |
| `/rebase <PR>` | `/rb` | Rebase a PR onto its base branch | Yes |
| `/reviewrebase <PR>` | `/rr` | Review then rebase a PR (combo) | Yes |
| `/squash <PR>` | `/sq` | Squash all PR commits into one clean commit | Yes |
| `/recreate <PR>` | `/rc` | Re-implement a PR from scratch on a fresh branch | Yes |
| `/branches [project]` | `/br`, `/prs` | List koan branches + PRs with merge order | — |
| `/gh_request <url> <text>` | — | Route natural-language GitHub request to the right skill | Yes |
| `/done [project]` | `/merged` | List PRs merged in the last 24 hours | — |

## Exploration & Analysis

| Command | Aliases | Description |
|---------|---------|-------------|
| `/ai <topic>` | `/ia` | Queue an AI exploration mission (deep, with codebase access) |
| `/magic <topic>` | — | Instant creative exploration (quick, no mission queue) |
| `/sparring` | — | Strategic challenge session — thinking, not code |
| `/audit <project>` | — | Audit project, create GitHub issues for findings (top N, default 5) |
| `/security_audit <project>` | `/security`, `/secu` | Security audit, find critical vulnerabilities |
| `/private_security_audit <project>` | `/private_security`, `/psecu` | Security audit, findings to journal only (no GitHub) |
| `/gha_audit [project]` | `/gha` | Scan GitHub Actions workflows for security vulnerabilities |
| `/tech_debt [project]` | `/td`, `/debt` | Scan project for tech debt |
| `/dead_code [project]` | `/dc` | Scan for unused code |
| `/profile <project>` | `/perf`, `/benchmark` | Performance profiling mission |
| `/incident <error>` | — | Triage a production error from a stack trace |
| `/changelog [project]` | `/changes` | Generate changelog from recent commits and journal entries |
| `/stats [project]` | — | Show session outcome statistics per project |

## Ideas & Reflection

| Command | Aliases | Description |
|---------|---------|-------------|
| `/idea <text>` | `/buffer` | Add to the ideas backlog |
| `/ideas` | — | List all ideas |
| `/reflect <msg>` | `/think` | Write a reflection to the shared journal |
| `/journal [project] [date]` | `/log` | View journal entries |
| `/email` | — | Email status digest (use `/email test` to verify setup) |

## Status & Monitoring

| Command | Aliases | Description |
|---------|---------|-------------|
| `/status` | `/st` | Show agent status, missions, and loop health |
| `/ping` | — | Check if the agent loop is alive |
| `/usage` | — | Detailed quota and progress |
| `/metrics` | — | Mission success rates and reliability stats |
| `/quota` | `/q` | Check LLM quota (live, no cache) |
| `/live` | `/progress` | Show live progress from the current run |
| `/logs [run\|awake\|all]` | — | Show last 20 lines from logs |
| `/check_notifications` | `/read` | Force immediate GitHub + Jira notification check |
| `/doctor` | `/diag` | Run diagnostic self-checks on config and health |
| `/snapshot` | — | Export memory state to a portable snapshot file |

## Configuration

| Command | Aliases | Description |
|---------|---------|-------------|
| `/projects` | `/proj` | List configured projects |
| `/add_project <url>` | `/add`, `/addproject` | Clone a GitHub repo and add it to the workspace |
| `/delete_project <name>` | `/delete`, `/del` | Remove a project from workspace |
| `/rename <old> <new>` | `/rename_project` | Rename a project everywhere |
| `/focus [duration]` | — | Lock the agent to one project (suppress exploration) |
| `/unfocus` | — | Exit focus mode |
| `/passive [duration]` | — | Enter read-only passive mode |
| `/active` | — | Exit passive mode, resume execution |
| `/explore [project]` | `/exploration` | Enable/show exploration mode |
| `/noexplore [project]` | — | Disable exploration mode |
| `/language <lang>` | `/lng` | Set reply language preference |
| `/french` | `/fr`, `/francais`, `/français` | Switch to French |
| `/english` | `/en`, `/anglais` | Switch to English |
| `/verbose` | — | Enable real-time progress updates |
| `/silent` | — | Disable real-time progress updates |
| `/config_check` | `/cfgcheck`, `/configcheck` | Detect drift between instance/config.yaml and the template |

## System

| Command | Aliases | Description |
|---------|---------|-------------|
| `/pause` | `/sleep` | Pause mission processing |
| `/resume` | `/work`, `/awake`, `/run`, `/start` | Resume mission processing |
| `/shutdown` | — | Shutdown both agent loop and messaging bridge |
| `/update` | `/upgrade` | Finish mission, update to latest upstream, restart |
| `/restart` | — | Restart processes (no code pull) |
| `/scaffold_skill <scope> <name> <desc>` | `/scaffold`, `/new_skill` | Generate SKILL.md + handler.py for a new custom skill |
| `/rtk [setup\|uninstall\|gain\|on\|off]` | — | Manage optional [rtk](https://github.com/rtk-ai/rtk) integration |

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

Or create your own in `instance/skills/<scope>/<name>/` with a `SKILL.md` file. See [koan/skills/README.md](../koan/skills/README.md) for the full authoring guide.
