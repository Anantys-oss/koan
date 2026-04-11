# Kōan User Manual

**From beginner to power user — everything Kōan can do.**

This manual is organized in three progressive tiers. Start with the basics, then unlock more advanced workflows as you grow comfortable.

> **New here?** Make sure you've completed the [Quick Start](../README.md#quick-start) or [Full Install Guide](../INSTALL.md) first. This manual assumes Kōan is already running.

---

## Table of Contents

- [Beginner — Daily Basics](#beginner--daily-basics)
  - [Your First Mission](#your-first-mission)
  - [Mission Lifecycle](#mission-lifecycle)
  - [Chatting with Kōan](#chatting-with-kōan)
  - [Managing Your Queue](#managing-your-queue)
  - [Checking Progress](#checking-progress)
  - [Branch Isolation & Reviewing Work](#branch-isolation--reviewing-work)
  - [Multi-Project Basics](#multi-project-basics)
- [Intermediate — Productivity Workflows](#intermediate--productivity-workflows)
  - [Code Operations](#code-operations)
  - [PR Management](#pr-management)
  - [Project Maintenance](#project-maintenance)
  - [Scheduling Work](#scheduling-work)
  - [Ideas Backlog](#ideas-backlog)
  - [Reflection & Journal](#reflection--journal)
  - [Email Digests](#email-digests)
  - [Statistics](#statistics)
  - [Understanding Quota Modes](#understanding-quota-modes)
  - [Exploration Mode](#exploration-mode)
  - [Workflow Example: Feature from Idea to PR](#workflow-example-feature-from-idea-to-pr)
- [Power User — Advanced Configuration](#power-user--advanced-configuration)
  - [Parallel Sessions](#parallel-sessions)
  - [Deep Exploration](#deep-exploration)
  - [Configuration Deep-Dive](#configuration-deep-dive)
  - [Per-Project Overrides](#per-project-overrides)
  - [Custom Skills](#custom-skills)
  - [GitHub @mention Integration](#github-mention-integration)
  - [CLI Providers](#cli-providers)
  - [Language Preference](#language-preference)
  - [System Management](#system-management)
  - [Memory System](#memory-system)
  - [Personality Customization](#personality-customization)
  - [Auto-Update](#auto-update)
  - [Adding New Projects](#adding-new-projects)
  - [Performance Profiling](#performance-profiling)
  - [Incident Triage](#incident-triage)
  - [Web Dashboard](#web-dashboard)
  - [Deployment](#deployment)
- [Quick Reference](#quick-reference)

---

## Beginner — Daily Basics

Everything you need to use Kōan day-to-day. If you've just installed Kōan, start here.

### Your First Mission

Send a message to Kōan via Telegram (or Slack). If it looks like a task, Kōan automatically queues it as a mission:

> *"Audit the auth module for security issues"*

For explicit control, use the `/mission` command:

```
/mission Refactor the payment service to use async/await
```

**`/mission`** — Queue a new mission for the agent to work on.

- **Usage:** `/mission <description>`
- **Options:**
  - `/mission --now <description>` — Insert at the top of the queue (next to run)
  - `/mission [project:webapp] <description>` — Target a specific project

<details>
<summary>Use cases</summary>

- `/mission Add input validation to the signup form` — Queue a feature task
- `/mission --now Fix the broken CI pipeline` — Urgent fix, skip the queue
- `/mission [project:api] Write integration tests for the /users endpoint` — Target a specific project
</details>

### Mission Lifecycle

Every mission flows through a simple lifecycle:

```
Pending  →  In Progress  →  Done ✓
                          →  Failed ✗
```

1. **Pending** — Queued and waiting. Kōan picks missions from the top of the queue.
2. **In Progress** — Kōan is actively working on it via the configured CLI provider.
3. **Done** — Completed successfully. Code is in a `koan/*` branch, often with a draft PR.
4. **Failed** — Something went wrong. Kōan logs the reason and moves on.

By default, Kōan processes one mission at a time. When idle, it picks the next pending mission automatically. For concurrent execution, see [Parallel Sessions](#parallel-sessions).

### Chatting with Kōan

Just send a regular message — Kōan classifies it automatically. Short conversational messages get instant replies (chat mode). Task-like messages get queued as missions.

If Kōan misclassifies your message, use `/chat` to force chat mode:

**`/chat`** — Force a message to be treated as chat, not a mission.

- **Usage:** `/chat <message>`

<details>
<summary>Use cases</summary>

- `/chat What do you think about using Redis for caching?` — Ask for an opinion without creating a mission
- `/chat How's your day going?` — Just talk
</details>

### Managing Your Queue

**`/list`** — See all pending and in-progress missions.

- **Aliases:** `/queue`, `/ls`

<details>
<summary>Use cases</summary>

- `/list` — Check what's queued up before adding more work
- `/ls` — Quick glance at the queue
</details>

**`/cancel`** — Remove a pending mission from the queue.

- **Usage:** `/cancel <number>` or `/cancel <keyword>`
- **Aliases:** `/remove`, `/clear`

<details>
<summary>Use cases</summary>

- `/cancel 3` — Cancel the 3rd pending mission
- `/cancel auth` — Cancel the mission matching "auth"
</details>

**`/abort`** — Abort the current in-progress mission and move to the next one.

- **Usage:** `/abort`
- The running Claude subprocess is killed, the mission is moved to Failed, and the agent loop picks the next pending item.

**`/priority`** — Move a pending mission to a different position in the queue.

- **Usage:** `/priority <n>` (move to top) or `/priority <n> <position>`

<details>
<summary>Use cases</summary>

- `/priority 5` — Move mission #5 to the top of the queue
- `/priority 3 2` — Move mission #3 to position #2
</details>

### Checking Progress

**`/status`** — Get a quick overview of Kōan's state: what's running, what's queued, loop health.

- **Aliases:** `/st`
- **Related:** `/ping` (is the loop alive?), `/usage` (detailed quota), `/metrics` (success rates)

<details>
<summary>Use cases</summary>

- `/status` — "Is Kōan working? What's it doing?"
- `/ping` — Quick health check
- `/metrics` — See mission success/failure rates
</details>

**`/live`** — See real-time progress from the currently running mission.

- **Aliases:** `/progress`

<details>
<summary>Use cases</summary>

- `/live` — Check what Kōan is doing right now during a long mission
</details>

**`/logs [run|awake|all]`** — Show the last 20 lines from log files, formatted in code blocks.

- **Default:** Shows only `run.log`. Use `awake` for bridge logs, `all` for both.

<details>
<summary>Use cases</summary>

- `/logs` — Quick check of recent agent output (run.log only)
- `/logs awake` — Check bridge/Telegram polling output
- `/logs all` — See both run and awake logs
</details>

**`/quota [remaining_%]`** — Check remaining API quota (live, no cache), or override the internal estimate.

- **Aliases:** `/q`

<details>
<summary>Use cases</summary>

- `/quota` — See how much API budget is left before adding heavy missions
- `/quota 32` — Tell Kōan it has 32% remaining (fixes drift when internal estimate is wrong)
- If Kōan is paused due to quota but the API is actually available, `/quota 50` will correct the estimate and clear the pause
</details>

**`/verbose`** / **`/silent`** — Toggle real-time progress updates. When verbose is on, Kōan sends progress messages as it works.

<details>
<summary>Use cases</summary>

- `/verbose` — Turn on updates when you want to follow along
- `/silent` — Turn off updates when you're busy (default)
</details>

### Branch Isolation & Reviewing Work

Kōan **never commits to `main`**. All work happens in `koan/*` branches (the prefix is configurable). After completing a mission, Kōan typically:

1. Creates a branch like `koan/refactor-payment-service`
2. Commits changes with clear messages
3. Pushes the branch and creates a **draft PR**

Your workflow:

```bash
# See what Kōan produced
git log koan/refactor-payment-service

# Review the PR on GitHub
# Merge when you're satisfied — or ask Kōan to iterate
```

**The agent proposes. The human decides.** — You always have the final say.

### Multi-Project Basics

Kōan can manage multiple projects simultaneously. It rotates between them based on queue priority and quota.

**`/projects`** — List all configured projects.

- **Aliases:** `/proj`

<details>
<summary>Use cases</summary>

- `/projects` — See which repos Kōan is managing
</details>

**`/focus`** — Lock Kōan to a single project. While focused, it only processes missions for that project and skips exploration/reflection.

- **Usage:** `/focus [duration]` (default: 5 hours)
- **Examples:** `/focus`, `/focus 3h`, `/focus 2h30m`

**`/unfocus`** — Exit focus mode, resume normal multi-project rotation.

<details>
<summary>Use cases</summary>

- `/focus` — "I need all attention on the webapp for the next few hours"
- `/focus 1h` — Short focused sprint
- `/unfocus` — "OK, back to normal"
</details>

**`/passive`** — Enter passive (read-only) mode. The agent loop keeps running (heartbeat, GitHub notification polling, Telegram commands) but never executes missions or autonomous work. Missions accumulate as Pending.

- **Usage:** `/passive [duration]` — no duration = indefinite
- **Examples:** `/passive`, `/passive 4h`, `/passive 2h30m`

**`/active`** — Exit passive mode and resume normal execution. Queued missions drain naturally.

<details>
<summary>Use cases</summary>

- `/passive` — "I'm at the desk, don't touch anything"
- `/passive 4h` — "Hands off for the next 4 hours"
- `/active` — "I'm done, you can work again"
</details>

---

## Intermediate — Productivity Workflows

These features turn Kōan from a task runner into a full development workflow partner.

### Code Operations

**`/brainstorm`** — Decompose a broad topic into multiple linked GitHub issues grouped under a master tracking issue.

- **Usage:** `/brainstorm <topic>`, `/brainstorm <project> <topic>`, `/brainstorm <topic> --tag <label>`
- **GitHub @mention:** `@koan-bot /brainstorm <topic>` on an issue

<details>
<summary>Use cases</summary>

- `/brainstorm Improve caching strategy for API responses` — Creates 3-8 sub-issues + master issue
- `/brainstorm koan Add observability and monitoring` — Target a specific project
- `/brainstorm Refactor auth module --tag auth-refactor` — With explicit tag for grouping
</details>

**`/plan`** — Deep-think an idea and produce a structured implementation plan as a GitHub issue.

- **Usage:** `/plan <idea>`, `/plan <project> <idea>`, `/plan <issue-url>` (iterate on existing)
- **GitHub @mention:** `@koan-bot /plan <idea>` on an issue

<details>
<summary>Use cases</summary>

- `/plan Add WebSocket support for real-time notifications` — Get a phased plan before writing any code
- `/plan https://github.com/org/repo/issues/42` — Iterate on an existing issue's plan
- `/plan webapp Add rate limiting to public API endpoints` — Target a specific project
</details>

**`/deepplan`** — Spec-first design with Socratic exploration of 2-3 approaches before planning. For complex missions where design matters more than speed.

- **Usage:** `/deepplan <idea>`, `/deepplan <project> <idea>`, `/deepplan <github-issue-url>`
- **Aliases:** `/deeplan`
- **GitHub @mention:** `@koan-bot /deepplan <idea>` on an issue

The workflow: (1) explores your codebase and surfaces 2-3 distinct design approaches with trade-offs, (2) runs a spec review loop (up to 5 iterations) to ensure the spec is concrete and complete, (3) posts the approved spec as a GitHub issue, (4) queues a `/plan <issue-url>` mission for your review and approval.

When given a GitHub issue URL, the project is automatically detected from the repository and the issue title, body, and all comments are fetched to provide full context for the design exploration.

Use this before `/plan` when the idea is architecturally complex, when you want to explore alternatives before committing, or when design mistakes would be expensive to fix later.

<details>
<summary>Use cases</summary>

- `/deepplan Refactor the auth middleware to support OAuth2` — Explore design approaches before writing any code
- `/deepplan koan Add multi-tenant project isolation` — Target a specific project with spec-first design
- `/deepplan https://github.com/org/repo/issues/42` — Deep plan from an existing GitHub issue with full context
- `/deepplan Redesign the mission queue for concurrent execution` — Surface trade-offs for a complex architectural change
</details>

**`/implement`** — Queue an implementation mission for a GitHub issue.

- **Usage:** `/implement <issue-url> [additional context]`
- **Aliases:** `/impl`
- **GitHub @mention:** `@koan-bot /implement` on an issue

<details>
<summary>Use cases</summary>

- `/implement https://github.com/org/repo/issues/42` — Implement what the issue describes
- `/implement https://github.com/org/repo/issues/42 Focus on the backend only` — Add guidance
</details>

**`/fix`** — Fix a GitHub issue end-to-end: understand, plan, test, implement, and submit a PR.

- **Usage:** `/fix <issue-url> [additional context]`
- **GitHub @mention:** `@koan-bot /fix` on an issue

<details>
<summary>Use cases</summary>

- `/fix https://github.com/org/repo/issues/99` — Full bug-fix pipeline
- `/fix https://github.com/org/repo/issues/99 Regression from v2.3` — Provide extra context
</details>

**`/review`** — Queue a code review for a pull request or issue.

- **Usage:** `/review <github-pr-or-issue-url> [--architecture]`
- **Aliases:** `/rv`
- **GitHub @mention:** `@koan-bot /review` on a PR
- **Flags:**
  - `--architecture` — Architecture-focused review (SOLID principles, layering, coupling, abstraction boundaries)

<details>
<summary>Use cases</summary>

- `/review https://github.com/org/repo/pull/55` — Get a thorough code review
- `/rv https://github.com/org/repo/pull/55` — Same thing, shorter
- `/review https://github.com/org/repo/pull/55 --architecture` — Architecture-focused review
</details>

**`/refactor`** — Queue a targeted refactoring mission.

- **Usage:** `/refactor <github-url-or-path>`
- **Aliases:** `/rf`
- **GitHub @mention:** `@koan-bot /refactor` on a PR or issue

<details>
<summary>Use cases</summary>

- `/refactor https://github.com/org/repo/pull/60` — Refactor code in a PR
- `/rf https://github.com/org/repo/issues/70` — Refactor based on an issue description
</details>

### PR Management

**`/ask`** — Ask a question about a GitHub PR or issue and get an AI-generated reply posted directly to the thread.

- **Usage:** `/ask <github-comment-url>`
- **GitHub @mention:** `@koan-bot ask <your question>` on any PR or issue

<details>
<summary>Use cases</summary>

- `@koan-bot ask why does this test fail?` — Kōan investigates the thread context and replies on GitHub
- `@koan-bot ask what is the purpose of this PR?` — Get a structured explanation with context summary
- `/ask https://github.com/org/repo/issues/42#issuecomment-123456` — Reply to a specific comment
</details>

**`/rebase`** — Rebase a PR onto its base branch.

- **Usage:** `/rebase <pr-url>`
- **Aliases:** `/rb`
- **GitHub @mention:** `@koan-bot /rebase` on a PR

<details>
<summary>Use cases</summary>

- `/rebase https://github.com/org/repo/pull/42` — Resolve conflicts and update the PR
</details>

**`/reviewrebase`** — Review a PR then rebase it, so review insights feed the rebase.

- **Usage:** `/reviewrebase <pr-url>`
- **Aliases:** `/rr`
- **GitHub @mention:** `@koan-bot /rr` on a PR

<details>
<summary>Use cases</summary>

- `/rr https://github.com/org/repo/pull/42` — Queues `/review` then `/rebase` in sequence
- Extra context after the URL is passed to the review step (e.g., `/rr <url> focus on error handling`)
</details>

**`/squash`** — Squash all PR commits into a single clean commit.

- **Usage:** `/squash <pr-url>`
- **Aliases:** `/sq`
- **GitHub @mention:** `@koan-bot /squash` on a PR

<details>
<summary>Use cases</summary>

- `/squash https://github.com/org/repo/pull/42` — Clean up messy commit history before merge
</details>

**`/recreate`** — Re-implement a PR from scratch on a fresh branch. Useful when a PR has diverged too far.

- **Usage:** `/recreate <pr-url>`
- **Aliases:** `/rc`
- **GitHub @mention:** `@koan-bot /recreate` on a PR

<details>
<summary>Use cases</summary>

- `/recreate https://github.com/org/repo/pull/42` — Start fresh when rebasing won't cut it
</details>

**`/pr`** — Review and update a GitHub pull request (interactive).

- **Usage:** `/pr <pr-url>`

<details>
<summary>Use cases</summary>

- `/pr https://github.com/org/repo/pull/55` — Review a PR and apply updates
</details>

**`/branches`** — List koan branches and open PRs with recommended merge order and stats.

- **Usage:** `/branches [project_name]`
- **Aliases:** `/br`, `/prs`

<details>
<summary>Use cases</summary>

- `/branches` — Show all koan branches for the default project with merge recommendations
- `/branches koan` — Show branches for a specific project
</details>

**`/check`** — Run project health checks on a PR or issue (rebase, review, plan as needed).

- **Usage:** `/check <pr-or-issue-url>`
- **Aliases:** `/inspect`

The check loop also **auto-forwards unresolved human review comments** on Kōan-created PRs. When a reviewer leaves comments, `/check` detects them and creates a mission to address the feedback — no explicit @mention required. Fingerprint-based deduplication (SHA-256 of sorted comment IDs) prevents re-dispatching the same set of comments across repeated checks. Bot comments (Codecov, Dependabot, etc.) are filtered out automatically.

Configure this behavior in `config.yaml`:

```yaml
review_dispatch:
  include_drafts: true   # Also dispatch for draft PRs (default: true)
```

<details>
<summary>Use cases</summary>

- `/check https://github.com/org/repo/pull/42` — Let Kōan decide what a PR needs
- Reviewer leaves comments on a PR → next `/check` run creates a mission to address them
</details>

**`/ci_check`** — Check and fix CI failures on a GitHub PR using Claude.

- **Usage:** `/ci_check <pr-url>`

Usually auto-triggered when CI fails after a `/rebase`, but can also be invoked manually. Fetches failure logs, checks out the PR branch, and runs Claude to attempt a fix. If the fix produces a commit, it force-pushes and re-enqueues the PR for CI monitoring.

<details>
<summary>Use cases</summary>

- `/ci_check https://github.com/org/repo/pull/42` — Attempt to fix CI failures on a PR
- Auto-injected by the CI queue when a post-rebase CI run fails
</details>

**`/ci_recovery`** — Show CI failure recovery status for all open Kōan PRs.

- **Usage:** `/ci_recovery`
- **Aliases:** `/ci_fix`

<details>
<summary>Use cases</summary>

- `/ci_recovery` — See which PRs have active CI recovery sessions, attempt counts, and last attempt timestamps
</details>

**`/gh_request`** — Route a natural-language GitHub request to the appropriate action.

- **Usage:** `/gh_request <github-url> <request text>`
- **GitHub @mention:** Used automatically when `natural_language: true` is enabled — free-form @mentions are routed here instead of failing with URL validation errors.

<details>
<summary>Use cases</summary>

- `/gh_request https://github.com/org/repo/pull/42 can you review this?` — Classifies as `/review` and queues
- `/gh_request https://github.com/org/repo/issues/10 please fix this` — Classifies as `/fix` and queues
- `@koan-bot can you rebase this PR?` — Automatically routed to `/gh_request` when `natural_language` is on
</details>

### Project Maintenance

**`/claudemd`** — Refresh or create a project's `CLAUDE.md` based on recent architectural changes.

- **Usage:** `/claudemd [project-name]`
- **Aliases:** `/claude`, `/claude.md`, `/claude_md`

<details>
<summary>Use cases</summary>

- `/claudemd webapp` — Update the CLAUDE.md after a big refactor
- `/claudemd` — Refresh for the default/focused project
</details>

**`/gha_audit`** — Scan GitHub Actions workflows for security vulnerabilities.

- **Usage:** `/gha_audit [project-name]`
- **Aliases:** `/gha`

<details>
<summary>Use cases</summary>

- `/gha_audit` — Quick security check of your CI/CD pipelines
- `/gha_audit api` — Audit a specific project's workflows
</details>

**`/changelog`** — Generate a changelog from recent commits and journal entries.

- **Usage:** `/changelog [project] [--since=YYYY-MM-DD] [--format=md|telegram]`
- **Aliases:** `/changes`

<details>
<summary>Use cases</summary>

- `/changelog` — What changed recently?
- `/changelog webapp --since=2025-01-01` — Changes since a specific date
- `/changelog --format=md` — Get markdown output for release notes
</details>

**`/done`** — List PRs merged in the last 24 hours across all projects.

- **Usage:** `/done [project] [--hours=N]`
- **Aliases:** `/merged`

<details>
<summary>Use cases</summary>

- `/done` — What got merged today?
- `/done webapp` — Merged PRs for a specific project
- `/done --hours=48` — Merged PRs in the last 2 days
</details>

### Scheduling Work

Kōan supports recurring missions that automatically re-queue at set intervals.

**`/daily`** — Schedule a mission to run every day.
- **Usage:** `/daily <text> [project:<name>]`

**`/hourly`** — Schedule a mission to run every hour.
- **Usage:** `/hourly <text> [project:<name>]`

**`/weekly`** — Schedule a mission to run every week.
- **Usage:** `/weekly <text> [project:<name>]`

**`/recurring`** — List all active recurring missions.

**`/cancel_recurring`** — Cancel a recurring mission.
- **Usage:** `/cancel_recurring <n>` or `/cancel_recurring <keyword>`
- **Aliases:** —

<details>
<summary>Use cases</summary>

- `/daily Review open PRs and summarize status [project:webapp]` — Daily PR digest
- `/weekly Run the full test suite and report flaky tests` — Weekly health check
- `/hourly Check CI status [project:api]` — Frequent monitoring
- `/recurring` — See what's scheduled
- `/cancel_recurring 2` — Stop a recurring mission
</details>

### Ideas Backlog

Not ready to commit to a mission? Save it as an idea.

**`/idea`** — Add an idea to the backlog, or manage existing ideas.

- **Usage:**
  - `/idea <text>` — Add a new idea
  - `/idea <project> <text>` — Add idea for a specific project
  - `/idea promote <n>` — Promote idea #n to a mission
  - `/idea delete <n>` — Delete idea #n
- **Aliases:** `/buffer`

**`/ideas`** — List all ideas in the backlog.

<details>
<summary>Use cases</summary>

- `/idea Maybe we should add GraphQL support` — Save for later
- `/ideas` — Browse the backlog
- `/idea promote 3` — "OK, let's do idea #3"
</details>

### Reflection & Journal

**`/reflect`** — Write a reflection to the shared journal. Both you and Kōan contribute to this shared space.

- **Usage:** `/reflect <observation>`
- **Aliases:** `/think`

<details>
<summary>Use cases</summary>

- `/reflect The new caching layer reduced API latency by 40%` — Share an observation
- `/reflect I think we should prioritize mobile performance next quarter`
</details>

**`/journal`** — View journal entries.

- **Usage:** `/journal [project] [date]`
- **Aliases:** `/log`

<details>
<summary>Use cases</summary>

- `/journal` — Today's journal entries
- `/journal webapp` — Journal for a specific project
- `/journal 2025-03-01` — Historical entries
</details>

### Email Digests

**`/email`** — Check email digest status or send a test email.

- **Usage:** `/email`, `/email test`

<details>
<summary>Use cases</summary>

- `/email` — Check if email digests are configured
- `/email test` — Send a test email to verify setup
</details>

### Statistics

**`/stats`** — View session outcome statistics per project: success rates, mission counts, productivity trends.

- **Usage:** `/stats [project]`

<details>
<summary>Use cases</summary>

- `/stats` — Overall productivity snapshot
- `/stats webapp` — How's Kōan doing on a specific project?
</details>

### Understanding Quota Modes

Kōan automatically adapts its work intensity based on remaining API quota:

| Mode | Quota | Behavior |
|------|-------|----------|
| **DEEP** | >40% | Strategic work, thorough exploration, comprehensive reviews |
| **IMPLEMENT** | 15–40% | Focused development, quick wins, efficient execution |
| **REVIEW** | <15% | Read-only analysis, code audits, lightweight tasks |
| **WAIT** | <5% | Graceful pause until quota resets |

You don't need to manage this — Kōan adjusts automatically. Use `/quota` to see the current mode. If the internal estimate drifts from reality, use `/quota <N>` to override (e.g., `/quota 50` tells Kōan it has 50% remaining).

### Exploration Mode

When exploration is enabled, Kōan may autonomously explore a project's codebase between missions — discovering improvements, noting issues, and building context.

**`/explore`** — Enable exploration or show status.
- **Usage:** `/explore [project|all|none]`
- **Aliases:** `/exploration`

**`/noexplore`** — Disable exploration for a project.
- **Usage:** `/noexplore [project]`

<details>
<summary>Use cases</summary>

- `/explore webapp` — Let Kōan explore the webapp codebase
- `/explore all` — Enable exploration for all projects
- `/noexplore` — Disable exploration (focus on missions only)
</details>

### Workflow Example: Feature from Idea to PR

Here's a typical multi-step workflow combining several commands:

```
1. /idea Add rate limiting to the public API          # Save the idea
2. /idea promote 1                                     # Ready to work on it
3. /plan Add rate limiting to the public API           # Get a structured plan
4. /implement https://github.com/org/repo/issues/123   # Implement the plan
5. /review https://github.com/org/repo/pull/124        # Review the result
6. # Merge the PR on GitHub when satisfied
```

---

## Power User — Advanced Configuration

Unlock Kōan's full potential with advanced configuration and extensibility features.

### Parallel Sessions

Kōan can work on multiple missions simultaneously using **git worktrees** for isolation. Each parallel session runs in its own worktree with a dedicated branch, so sessions never interfere with each other.

#### How It Works

When parallel sessions are enabled, Kōan can pick up additional pending missions while one is already running. Each session gets:

- **Isolated worktree** — a separate checkout of the repository under `.worktrees/`
- **Dedicated branch** — `koan/session-<id>` branches created automatically
- **Independent subprocess** — a Claude Code process running in the worktree

Sessions are coordinated through a persistent registry (`instance/sessions.json`) with file-level locking for process safety.

#### Configuration

Add `max_parallel_sessions` to your `instance/config.yaml`:

```yaml
# Parallel session configuration
max_parallel_sessions: 2    # Number of concurrent sessions (1-5, default: 2)
```

Set to `1` to disable parallel execution and use the classic sequential mode.

#### Shared Dependencies

To avoid duplicating heavy dependency directories across worktrees, configure `shared_deps` in your project's `projects.yaml`:

```yaml
projects:
  webapp:
    path: ~/Code/webapp
    shared_deps:
      - node_modules
      - .venv
```

These directories are symlinked from the main project into each worktree, saving disk space and setup time.

> **Note:** Shared deps are best used for read-only caches. If a mission's build step modifies dependencies (e.g., `npm install`), it may affect other sessions sharing the same directory.

#### Monitoring

Parallel sessions appear in the standard status commands:

- **`/status`** — Shows count of active parallel sessions
- **`/live`** — Shows progress of all running sessions

Session output is captured to temporary files and collected when each session completes.

#### Cleanup

Worktrees and session branches are automatically cleaned up when a session finishes (success or failure). On startup, Kōan also recovers stale sessions from previous crashes — marking them as failed and removing their worktrees.

To manually clean up orphaned worktrees:

```bash
# From the project directory
git worktree list    # See all worktrees
git worktree prune   # Remove stale references
```

### Deep Exploration

**`/ai`** — Queue an AI exploration mission. Runs as a full agent mission with codebase access — deeper and more thorough than `/magic`.

- **Usage:** `/ai [project]`
- **Aliases:** `/ia`

<details>
<summary>Use cases</summary>

- `/ai webapp` — Deep dive into a project, discover insights, suggest improvements
- `/ai` — Explore the default/focused project
</details>

**`/magic`** — Instant creative exploration. Quick single-turn analysis without queuing a mission.

- **Usage:** `/magic [project]`

<details>
<summary>Use cases</summary>

- `/magic` — "Surprise me — what's interesting in this codebase?"
- `/magic api` — Quick creative scan of a specific project
</details>

**`/sparring`** — Start a strategic sparring session. This is about thinking, not code — Kōan challenges your assumptions and pushes your ideas.

<details>
<summary>Use cases</summary>

- `/sparring` — "Challenge me on my architecture decisions"
</details>

### Configuration Deep-Dive

All behavioral config lives in `instance/config.yaml`. Key settings:

```yaml
# Work intensity
max_runs_per_day: 10          # Max missions per day
interval_seconds: 60          # Seconds between mission checks

# Model selection
models:
  mission: null               # Default (sonnet) for mission work
  chat: null                  # Default for chat replies
  lightweight: haiku          # Quick tasks (formatting, picking)

# Budget thresholds
budget:
  warn_at_percent: 20         # Warn when quota drops below
  stop_at_percent: 5          # Stop working below this

# Tool restrictions (limit what the agent can do)
tools:
  allowed: []                 # Whitelist (empty = all allowed)
  blocked: []                 # Blacklist specific tools

# Start on pause — boot directly into pause mode
# Useful for scheduled launches (cron, launchd) where you want
# the stack running but idle until you explicitly /resume.
start_on_pause: false

# Schedule (when Kōan is allowed to work)
schedule:
  timezone: UTC
  active_hours: "00:00-23:59" # Default: always active

# Skill execution limits
skill_timeout: 3600           # Max seconds for /fix, /implement, /incident
skill_max_turns: 200          # Max agentic turns for heavy skills

# Prompt guard (content safety)
prompt_guard: true            # Enable prompt injection detection

# Review ignore — exclude files from /review PR diffs
# Reduces token spend on generated/vendored code
# review_ignore:
#   glob:
#     - "vendor/**"    # all files under vendor/
#     - "*.lock"       # lock files at any depth
#   regex:
#     - '.*\.pb\.go$'  # protobuf-generated files (full path regex)
```

See `instance.example/config.yaml` for all available options.

**`/config_check`** — Detect drift between your `instance/config.yaml` and the template at `instance.example/config.yaml`. Reports two things:

- **Missing keys** — in the template but absent from your config. These are new features released since you last synced and are probably worth reviewing.
- **Extra keys** — in your config but absent from the template. These are usually deprecated/removed settings (or typos).

Run it after every Kōan update to stay in sync:

```
/config_check
```

The same check runs automatically as part of `/doctor` — use `/config_check` when you only want the config slice without the rest of the diagnostic report.

### Per-Project Overrides

Projects are configured in `projects.yaml` at `KOAN_ROOT`. Each project can override defaults:

```yaml
defaults:
  git_auto_merge:
    enabled: false
    strategy: squash

projects:
  webapp:
    path: ~/Code/webapp
    cli_provider: claude       # CLI provider override
    models:
      mission: opus            # Use Opus for this project
    tools:
      blocked: [WebSearch]     # Restrict certain tools
    git_auto_merge:
      enabled: true            # Auto-merge for this project
      strategy: squash
    authorized_users:          # Who can trigger via GitHub @mention
      - username1
```

Key per-project settings:
- **`cli_provider`** — `claude`, `codex`, `copilot`, `local`, or `ollama-launch`
- **`models`** — Override model selection per role
- **`tools`** — Restrict available tools
- **`git_auto_merge`** — Auto-merge completed PRs (strategy: squash/merge/rebase)
- **`authorized_users`** — GitHub users allowed to trigger via @mention
- **`exploration`** — Enable/disable autonomous exploration

### Custom Skills

Kōan's skill system is fully extensible. Install skills from Git repos or create your own.

**Install from Git:**
```
/skill install https://github.com/your-org/koan-skills.git
/skill update <scope>
/skill remove <scope>
```

**Create your own:** Add a `SKILL.md` file in `instance/skills/<scope>/<name>/`:

```yaml
---
name: my-skill
scope: my-scope
description: What this skill does
audience: bridge
commands:
  - name: mycommand
    description: One-line description
    usage: /mycommand <args>
handler: handler.py
---
```

The handler follows a simple pattern:

```python
def handle(ctx):
    # ctx.args — command arguments
    # ctx.project — current project
    # ctx.instance_dir — instance directory path
    return "Response message"  # or None for no reply
```

For prompt-only skills (no handler), put the prompt text after the YAML frontmatter — it's sent directly to Claude.

**Scaffold a skill from a description:**

Instead of writing SKILL.md and handler.py by hand, use `/scaffold_skill` to generate them:

```
/scaffold_skill myteam deploy Deploy to production with rollback support
```

This invokes Claude to produce a valid SKILL.md + handler.py stub in `instance/skills/myteam/deploy/`, validated against the parser before writing. Restart the bridge to load the new skill.

See [koan/skills/README.md](../koan/skills/README.md) for the full authoring guide.

### GitHub @mention Integration

Ten skills can be triggered by commenting `@koan-bot <command>` on GitHub issues and PRs:

| Skill | GitHub trigger |
|-------|---------------|
| `/brainstorm` | `@koan-bot /brainstorm <topic>` on an issue |
| `/implement` | `@koan-bot /implement` on an issue |
| `/fix` | `@koan-bot /fix` on an issue |
| `/review` | `@koan-bot /review` on a PR |
| `/rebase` | `@koan-bot /rebase` on a PR |
| `/reviewrebase` | `@koan-bot /rr` on a PR |
| `/recreate` | `@koan-bot /recreate` on a PR |
| `/refactor` | `@koan-bot /refactor` on a PR or issue |
| `/plan` | `@koan-bot /plan <idea>` on an issue |
| `/profile` | `@koan-bot /profile` on a PR or issue |

Setup requires configuring `github_nickname` and `github_commands_enabled` in `config.yaml`. See [docs/github-commands.md](github-commands.md) for full setup and configuration details.

### CLI Providers

Kōan supports multiple CLI backends. Configure globally via `KOAN_CLI_PROVIDER` env var or per-project in `projects.yaml`.

| Provider | Best for | Docs |
|----------|----------|------|
| **Claude Code** (default) | Full-featured agent, best reasoning | [provider-claude.md](provider-claude.md) |
| **OpenAI Codex** | ChatGPT users who want Codex models | [provider-codex.md](provider-codex.md) |
| **GitHub Copilot** | Teams with existing Copilot licenses | [provider-copilot.md](provider-copilot.md) |
| **Local LLM** | Offline, privacy, zero API cost | [provider-local.md](provider-local.md) |

### Language Preference

**`/language`** — Set or reset the reply language.

- **Usage:** `/language <lang>`, `/language reset`
- **Aliases:** `/lng`

**`/french`** / **`/english`** — Quick language switches.

- **Aliases:** `/fr`, `/francais`, `/français` / `/en`, `/anglais`

<details>
<summary>Use cases</summary>

- `/fr` — Switch to French replies
- `/en` — Switch back to English
- `/language reset` — Use default language
</details>

### System Management

**`/pause`** — Pause mission processing. Kōan stays running but won't pick up new missions.

- **Aliases:** `/sleep`

<details>
<summary>Use cases</summary>

- `/pause` — Temporarily stop mission work without shutting down
- Resume with `/resume` when ready
</details>

**`/resume`** — Resume mission processing after a pause (manual or automatic).

- **Aliases:** `/work`, `/awake`, `/run`, `/start`

<details>
<summary>Use cases</summary>

- `/resume` — Unpause after a manual `/pause` or quota exhaustion
</details>

**`/shutdown`** — Shutdown both the agent loop and the messaging bridge.

<details>
<summary>Use cases</summary>

- `/shutdown` — Gracefully stop everything (e.g., before system maintenance)
</details>

**`/update`** — Finish the current mission, pull updates, and restart.

- **Aliases:** `/upgrade`
- Graceful update: waits for the current mission to complete before pulling and restarting.
- If the update fails, Kōan still restarts (you asked for it).
- Use `/restart` if you just need a fresh start without pulling code.

<details>
<summary>Use cases</summary>

- `/update` — "Finish what you're doing, update yourself, and come back"
- `/upgrade` — Same as `/update`
</details>

**`/restart`** — Restart both agent and bridge processes without pulling new code.

<details>
<summary>Use cases</summary>

- `/restart` — Force a restart when Kōan is already up to date but you need a fresh start
</details>

**`/snapshot`** — Export memory state to a portable snapshot file for backup or migration.

<details>
<summary>Use cases</summary>

- `/snapshot` — Back up Kōan's memory before a major change
</details>

### Memory System

Kōan maintains persistent memory across sessions through several interconnected files:

- **`memory/summary.md`** — Global summary of learnings across all projects
- **`memory/projects/<name>/`** — Per-project learnings and context
- **`journal/YYYY-MM-DD/project.md`** — Daily logs of what Kōan did
- **`soul.md`** — Agent personality definition (see [Personality Customization](#personality-customization))

Memory is automatically compacted over time. Kōan uses it to build context for each mission, remembering past decisions, patterns, and mistakes.

### Personality Customization

Edit `instance/soul.md` to define Kōan's personality. This file shapes how Kōan communicates, what tone it uses, and what personality traits it exhibits. It's loaded into every interaction.

The design principle: code is generic and open source; instance data (including personality) is private. Fork the repo, write your own soul.

### Auto-Update

Kōan can automatically check for and apply updates from upstream. Configure in `config.yaml`:

```yaml
auto_update:
  enabled: true
  check_interval: 3600    # Seconds between checks
  notify: true             # Notify via Telegram when updating
```

See [docs/auto-update.md](auto-update.md) for details.

### Adding New Projects

**`/add_project`** — Clone a GitHub repo and add it to the workspace.

- **Usage:** `/add_project <github-url> [name]`
- **Aliases:** —

<details>
<summary>Use cases</summary>

- `/add_project https://github.com/org/new-repo` — Add a new repo for Kōan to manage
- `/add_project https://github.com/org/new-repo myproject` — Add with a custom name
</details>

### Removing Projects

**`/delete_project`** — Remove a project from the workspace.

- **Usage:** `/delete_project <project-name>`
- **Aliases:** `/delete`, `/del`

<details>
<summary>Use cases</summary>

- `/delete_project myrepo` — Remove a project directory and its projects.yaml entry
- `/del myrepo` — Same, using short alias
</details>

### Renaming Projects

**`/rename`** — Rename a project across all configuration and instance files.

- **Usage:** `/rename <old_name> <new_name>`
- **Aliases:** `/rename_project`

<details>
<summary>Use cases</summary>

- `/rename anantys-back aback` — Rename a project everywhere (projects.yaml, memory, journals, instance files)
- `/rename my-long-project mlp` — Shorten a project name for easier typing
</details>

### Performance Profiling

**`/profile`** — Queue a performance profiling mission for a project.

- **Usage:** `/profile <project-name-or-pr-url>`
- **Aliases:** `/perf`, `/benchmark`
- **GitHub @mention:** `@koan-bot /profile` on a PR or issue

<details>
<summary>Use cases</summary>

- `/profile webapp` — Profile the webapp project for performance issues
- `/profile https://github.com/org/repo/pull/42` — Profile changes in a PR
</details>

### Tech Debt Scan

**`/tech_debt`** — Scan a project for duplicated code, complex functions, testing gaps, and infrastructure issues. Produces a prioritized debt register saved to project learnings, and optionally queues the top improvement missions.

- **Usage:** `/tech_debt [project-name] [--no-queue]`
- **Aliases:** `/td`, `/debt`

<details>
<summary>Use cases</summary>

- `/tech_debt koan` — Scan the koan project for tech debt
- `/td webapp --no-queue` — Scan without queuing follow-up missions
- `/debt` — Scan the default project
</details>

### Dead Code Scan

**`/dead_code`** — Scan a project for unused imports, functions, classes, variables, and dead branches. Produces a certainty-classified report saved to project memory, and optionally queues the top removal missions.

- **Usage:** `/dead_code [project-name] [--no-queue]`
- **Aliases:** `/dc`

<details>
<summary>Use cases</summary>

- `/dead_code koan` — Scan the koan project for unused code
- `/dc webapp --no-queue` — Scan without queuing follow-up missions
- `/dead_code` — Scan the default project
</details>

### Codebase Audit

**`/audit`** — Audit a project for optimizations, simplifications, and potential issues. Creates a GitHub issue for each finding with detailed problem description, impact analysis, suggested fix, and severity/effort classification.

- **Usage:** `/audit <project-name> [extra context] [limit=N]`
- **GitHub @mention:** `@koan-bot /audit` on an issue or PR
- Default: top 5 most important findings. Use `limit=N` to override.

<details>
<summary>Use cases</summary>

- `/audit koan` — Full audit of the koan project (top 5 findings)
- `/audit webapp focus on the auth module` — Audit with specific focus
- `/audit mylib look for performance bottlenecks limit=10` — Targeted audit with custom limit
</details>

Each finding becomes a GitHub issue with:
- **Problem** — What's wrong and why it matters
- **Why This Matters** — Impact on bugs, performance, or maintainability
- **Suggested Fix** — Concrete description of what to change
- **Details table** — Severity, category, location, and effort estimate

### Security Audit

**`/security_audit`** — Perform a security-focused SDLC audit of a project. Searches for critical vulnerabilities (injection, auth flaws, secrets exposure, path traversal, SSRF, insecure deserialization, etc.) and creates a GitHub issue for each finding.

- **Usage:** `/security_audit <project-name> [extra context] [limit=N]`
- **Aliases:** `/security`, `/secu`
- **GitHub @mention:** `@koan-bot /security_audit` on an issue or PR
- Default: top 5 most critical findings. Use `limit=N` to override.

<details>
<summary>Use cases</summary>

- `/security_audit koan` — Full security audit (top 5 critical findings)
- `/security myapp focus on the API endpoints` — Security audit with specific focus
- `/secu webapp limit=3` — Quick security scan with custom limit
</details>

Each finding becomes a GitHub issue with:
- **Problem** — The vulnerability and how it could be exploited
- **Why This Matters** — Real-world impact (data breach, RCE, privilege escalation)
- **Suggested Fix** — Concrete remediation steps
- **Details table** — Severity, category, location, and effort estimate

### Incident Triage

**`/incident`** — Triage a production error from a stack trace or log snippet. Kōan will parse the error, identify the root cause, propose a fix with tests, and submit a draft PR.

- **Usage:** `/incident <error text or stack trace>`

<details>
<summary>Use cases</summary>

- `/incident TypeError: Cannot read property 'id' of undefined at UserService.getUser (user.js:42)` — Paste a stack trace and get a fix
</details>

### Web Dashboard

Run `make dashboard` to start a local web UI on port 5001. The dashboard provides:

- Real-time status overview
- Mission queue management
- Chat interface
- Journal browsing

The dashboard binds to `localhost` only — not accessible from the network.

### Deployment

For advanced deployment scenarios, see the existing documentation:

- [Docker deployment](docker.md)
- [SSH tunnel setup](ssh-setup.md)
- [Always-up Railway deployment](spec-always-up-railway.md)

---

## Quick Reference

All commands at a glance. **Tier:** B = Beginner, I = Intermediate, P = Power User.

| Command | Aliases | Tier | Description |
|---------|---------|:----:|-------------|
| `/mission <text>` | — | B | Queue a new mission (`--now` for top priority) |
| `/list` | `/queue`, `/ls` | B | List pending and in-progress missions |
| `/cancel <n>` | `/remove`, `/clear` | B | Cancel a pending mission |
| `/abort` | — | B | Abort current mission, pick next pending |
| `/priority <n>` | — | B | Reorder a pending mission in the queue |
| `/status` | `/st` | B | Quick status overview |
| `/ping` | — | B | Check if the agent loop is alive |
| `/usage` | — | B | Detailed quota and progress |
| `/metrics` | — | B | Mission success rates and reliability stats |
| `/live` | `/progress` | B | Show live progress of current mission |
| `/logs [run\|awake\|all]` | — | B | Show last 20 lines from logs (default: run) |
| `/quota [N]` | `/q` | B | Check LLM quota (live), or override remaining % |
| `/chat <msg>` | — | B | Force chat mode (bypass mission detection) |
| `/verbose` | — | B | Enable real-time progress updates |
| `/silent` | — | B | Disable real-time progress updates |
| `/projects` | `/proj` | B | List configured projects |
| `/focus [duration]` | — | B | Lock agent to one project |
| `/unfocus` | — | B | Exit focus mode |
| `/passive [duration]` | — | B | Enter read-only passive mode |
| `/active` | — | B | Exit passive mode, resume execution |
| `/brainstorm <topic>` | — | I | Decompose topic into linked sub-issues + master issue |
| `/plan <desc>` | — | I | Create a structured implementation plan |
| `/deepplan <idea\|issue-url>` | `/deeplan` | I | Spec-first design: explore approaches, post spec, queue /plan |
| `/implement <issue>` | `/impl` | I | Implement a GitHub issue |
| `/fix <issue>` | — | I | Full bug-fix pipeline (understand → plan → test → fix → PR) |
| `/review <PR> [--architecture]` | `/rv` | I | Review a pull request |
| `/refactor <desc>` | `/rf` | I | Targeted refactoring mission |
| `/ask <comment-url>` | — | I | Ask a question about a PR/issue — posts AI reply to GitHub |
| `/rebase <PR>` | `/rb` | I | Rebase a PR onto its base branch |
| `/reviewrebase <PR>` | `/rr` | I | Review then rebase a PR (combo) |
| `/squash <PR>` | `/sq` | I | Squash all PR commits into one clean commit |
| `/recreate <PR>` | `/rc` | I | Re-implement a PR from scratch |
| `/pr <PR>` | — | I | Review and update a GitHub PR |
| `/branches [project]` | `/br`, `/prs` | B | List koan branches + PRs with merge order |
| `/check <url>` | `/inspect` | I | Run project health checks on a PR/issue |
| `/ci_check <PR>` | — | I | Check and fix CI failures on a PR |
| `/ci_recovery` | `/ci_fix` | I | Show CI failure recovery status for open Kōan PRs |
| `/gh_request <url> <text>` | — | I | Route natural-language GitHub request to the right skill |
| `/claudemd [project]` | `/claude`, `/claude.md`, `/claude_md` | I | Refresh a project's CLAUDE.md |
| `/config_check` | `/cfgcheck`, `/configcheck` | P | Detect config.yaml drift against instance.example template |
| `/gha_audit [project]` | `/gha` | I | Audit GitHub Actions for security issues |
| `/changelog [project]` | `/changes` | I | Generate changelog from commits/journal |
| `/daily <text>` | — | I | Schedule a daily recurring mission |
| `/hourly <text>` | — | I | Schedule an hourly recurring mission |
| `/weekly <text>` | — | I | Schedule a weekly recurring mission |
| `/recurring` | — | I | List all recurring missions |
| `/cancel_recurring <n>` | `/cancel_recurring` | I | Cancel a recurring mission |
| `/idea <text>` | `/buffer` | I | Add to the ideas backlog |
| `/ideas` | — | I | List all ideas |
| `/reflect <msg>` | `/think` | I | Write a reflection to the shared journal |
| `/journal` | `/log` | I | View journal entries |
| `/email` | — | I | Email digest status or test |
| `/stats [project]` | — | I | Session outcome statistics |
| `/done [project]` | `/merged` | I | List PRs merged in the last 24 hours |
| `/explore [project]` | `/exploration` | I | Enable/show exploration mode |
| `/noexplore [project]` | — | I | Disable exploration mode |
| `/ai [project]` | `/ia` | P | Queue an AI exploration mission |
| `/magic [project]` | — | P | Instant creative exploration |
| `/sparring` | — | P | Strategic sparring session |
| `/language <lang>` | `/lng` | P | Set reply language |
| `/french` | `/fr`, `/francais`, `/français` | P | Switch to French |
| `/english` | `/en`, `/anglais` | P | Switch to English |
| `/pause` | `/sleep` | P | Pause mission processing |
| `/resume` | `/work`, `/awake`, `/run`, `/start` | P | Resume mission processing |
| `/shutdown` | — | P | Shutdown all processes |
| `/update` | `/upgrade` | P | Finish mission, update, restart |
| `/restart` | — | P | Restart processes (no code pull) |
| `/snapshot` | — | P | Export memory state |
| `/add_project <url>` | `/add_project` | P | Add a project from GitHub |
| `/delete_project <name>` | `/delete`, `/del` | P | Remove a project from workspace |
| `/rename <old> <new>` | `/rename_project` | P | Rename a project everywhere |
| `/profile <project>` | `/perf`, `/benchmark` | P | Performance profiling mission |
| `/audit <project> [ctx] [limit=N]` | — | P | Audit project, create GitHub issues (top N, default 5) |
| `/security_audit <project> [ctx] [limit=N]` | `/security`, `/secu` | P | Security audit, find critical vulnerabilities (top N, default 5) |
| `/tech_debt [project]` | `/td`, `/debt` | P | Scan project for tech debt |
| `/dead_code [project]` | `/dc` | P | Scan for unused code |
| `/incident <error>` | — | P | Triage a production error |
| `/scaffold_skill <scope> <name> <desc>` | `/scaffold`, `/new_skill` | P | Generate SKILL.md + handler.py for a new custom skill |

Skills marked with GitHub @mention support: `/audit`, `/security_audit`, `/brainstorm`, `/plan`, `/implement`, `/fix`, `/review`, `/rebase`, `/recreate`, `/refactor`, `/profile`, `/gh_request`. See [GitHub Commands](github-commands.md) for details.

---

*This manual covers all 42 core skills. For the full command reference with tabular format, see [docs/skills.md](skills.md). For skill authoring, see [koan/skills/README.md](../koan/skills/README.md).*
