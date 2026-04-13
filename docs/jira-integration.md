# Jira Integration

Control Koan directly from Jira issue comments using `@mention` commands.

> **Introduced in**: commit `fd3ccf8`. Enhanced with Jira URL support in skills, comment acknowledgment, and per-project target branches.

## Overview

Koan can poll your Jira Cloud instance for @mentions in issue comments. When a user posts:

```
@koan-bot plan
```

...in a Jira issue comment, Koan detects the mention, validates the command and the user's permissions, and queues a mission — all without webhooks or external services.

Jira-originated missions are marked with 🎫 in the mission queue (vs 📬 for GitHub-originated missions), making it easy to trace where a mission came from.

> **Jira + GitHub**: Both integrations can run simultaneously. See [Running Both Integrations](#running-both-integrations) below.

## Quick Start

### 1. Get a Jira API token

1. Go to [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**, give it a name (e.g. "Koan bot")
3. Copy the token

### 2. Configure Koan

In `instance/config.yaml`:

```yaml
jira:
  enabled: true
  base_url: "https://myorg.atlassian.net"
  email: "bot@example.com"
  nickname: "koan-bot"
  authorized_users: ["*"]
```

Set the API token via environment variable (recommended) or config:

```bash
# In .env
KOAN_JIRA_API_TOKEN=your-api-token-here
```

### 3. Map Jira projects to Koan projects

Tell Koan which Jira project keys correspond to which Koan projects:

```yaml
jira:
  projects:
    # Simple format — project name only:
    FOO: myproject        # FOO-123 → project "myproject"

    # Extended format — with optional target branch for PRs:
    BAR:
      project: anotherproject   # BAR-456 → project "anotherproject"
      branch: "11.126"          # PRs target branch "11.126" instead of repo default
```

Both formats can be mixed. The `branch` field is optional — when omitted, PRs target the repository's default branch as usual.

### 4. Post a command in a Jira issue comment

```
@koan-bot plan
```

Koan will:
1. Detect the @mention during its next polling cycle
2. Validate the command and user permissions
3. Create a pending mission: `- [project:myproject] /plan https://myorg.atlassian.net/browse/FOO-123 🎫`
4. Post a `👍 Mission queued: /plan` acknowledgment reply on the Jira comment
5. Send a Telegram notification confirming the mission was queued
6. Execute it in the next agent loop iteration — fetching the full Jira issue context (title, description, and all comments)

## Configuration Reference

All settings live under the `jira:` key in `instance/config.yaml`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Master switch for Jira integration |
| `base_url` | string | — | Jira instance URL (e.g. `https://myorg.atlassian.net`). **Required** when enabled |
| `email` | string | — | Atlassian account email for Basic auth. **Required** when enabled |
| `api_token` | string | — | Jira API token. Can also be set via `KOAN_JIRA_API_TOKEN` env var (takes precedence). **Required** when enabled |
| `nickname` | string | — | Bot's @mention name in Jira comments (without `@`). **Required** when enabled |
| `commands_enabled` | bool | `false` | Reserved for future per-command filtering |
| `authorized_users` | list | `[]` | `["*"]` = all users, or list of Jira account emails |
| `max_age_hours` | int | `24` | Ignore comments older than this (stale protection) |
| `check_interval_seconds` | int | `60` | Base polling interval in seconds (min: 10) |
| `max_check_interval_seconds` | int | `180` | Maximum backoff interval when idle (min: 30) |
| `projects` | dict | `{}` | Jira project key mapping. Simple: `FOO: myproject`. Extended: `FOO: {project: myproject, branch: "11.126"}` |

### Environment variables

| Variable | Purpose |
|----------|---------|
| `KOAN_JIRA_API_TOKEN` | Jira API token (overrides `jira.api_token` in config) |

### Startup validation

When `jira.enabled: true`, Koan validates the configuration at startup and warns if any required field is missing (`base_url`, `email`, `api_token`, `nickname`). The integration is silently skipped if `enabled: false`.

## Available Commands

Jira reuses the same `github_enabled: true` skill flag for command discovery — **both GitHub and Jira dispatch the exact same set of commands**. No separate Jira flag is needed.

> **Custom skills under `instance/skills/<scope>/`** (e.g. the cPanel integration shipping `/cp_fix` and `/cp_plan`) are exposed here the same way: set `github_enabled: true` and `group: integrations` in their SKILL.md. Such skills with a `handler.py` are dispatched **in-process** by the Jira bridge — not queued as slash missions — and the handler automatically receives the originating Jira issue key in `ctx.args` when the commenter omitted one. See `koan/skills/README.md` for the full pattern.

| Command | Aliases | What it does | Context-aware |
|---------|---------|--------------|---------------|
| `ask` | — | Ask Koan a question about a Jira issue | **Yes** |
| `audit` | — | Audit a project codebase and create GitHub issues | **Yes** |
| `brainstorm` | — | Decompose a topic into linked GitHub issues | **Yes** |
| `deepplan` | `deeplan` | Spec-first design with Socratic exploration | **Yes** |
| `fix` | — | Fix an issue end-to-end | **Yes** |
| `gh_request` | — | Natural-language GitHub request dispatch | **Yes** |
| `implement` | `impl` | Implement an issue | **Yes** |
| `plan` | — | Deep-think and create a structured plan | **Yes** |
| `profile` | `perf`, `benchmark` | Queue a performance profiling mission | **Yes** |
| `rebase` | `rb` | Rebase a PR onto latest upstream | **Yes** |
| `recreate` | `rc` | Recreate a diverged PR from scratch | **Yes** |
| `refactor` | `rf` | Queue a refactoring mission | **Yes** |
| `review` | `rv` | Queue a code review mission | **Yes** |
| `reviewrebase` | `rr` | Review then rebase combo | **Yes** |
| `security_audit` | `security`, `secu` | Security-focused audit | **Yes** |
| `squash` | `sq` | Squash all PR commits into one | **Yes** |

### Context-aware commands

Commands with context awareness accept additional text after the command word:

```
@koan-bot implement phase 1 only
```

This creates a mission: `/implement https://myorg.atlassian.net/browse/FOO-123 phase 1 only`

### Project override with `repo:`

You can override the default project mapping using the `repo:` token:

```
@koan-bot plan repo:other-project focus on API layer
```

This routes the mission to `other-project` instead of the project mapped to the Jira issue's project key.

### Branch override with `branch:`

You can override the target branch for PRs using the `branch:` token:

```
@koan-bot fix branch:main
```

This takes highest priority — overriding both the per-project `branch` configured in `jira.projects` and the repository's default branch. Useful for one-off requests targeting a different release branch.

When a target branch is set (via config or override), the feature branch is created from it and the PR targets it with `--base`.

## How It Works

### Architecture

```
run.py                       ← Pre-iteration check (before plan_iteration)
loop_manager.py              ← Also polls during sleep cycle (throttled, after GitHub check)
  ↓
jira_notifications.py        ← Fetches & filters Jira comments, parses @mentions
  ↓
jira_command_handler.py      ← Validates commands, checks permissions, creates missions
  ↓
jira_config.py               ← Reads jira: config (project map + branch map)
  ↓
skills.py                    ← Skill flags: github_enabled (reused for Jira)
```

### Notification processing flow

Jira notifications are checked in two places:
- **Pre-iteration**: At the start of each agent loop iteration (so `plan_iteration()` sees Jira missions immediately)
- **During sleep**: Between iterations (same as GitHub, with exponential backoff)

```
1. process_jira_notifications()
2. Build JQL query (POST /rest/api/3/search/jql): issues updated in mapped projects since last check
3. Paginate results using cursor-based nextPageToken
4. Fetch recent comments on matching issues
5. For each comment containing @nickname:
   a. Skip if already processed (in-memory set + .jira-processed.json)
   b. Skip if stale (> max_age_hours)
   c. Parse @mention → extract (command, context)
   d. Handle repo: override if present
   e. Handle branch: override if present (or use per-project config default)
   f. Validate command → skill must have github_enabled: true
   g. Check user permission → allowlist of Jira account emails
   h. Insert mission into missions.md (with branch:X token if set)
   i. Mark comment as processed (in-memory + persistent tracker)
   j. Post 👍 acknowledgment reply on the Jira comment
   k. Notify via Telegram (🎫 emoji prefix)
```

### ADF (Atlassian Document Format) handling

Jira Cloud stores comment bodies as ADF — a JSON tree format. Koan recursively extracts plain text from ADF nodes while skipping code blocks (`codeBlock`, `code`, `inlineCard`) to prevent false @mention matches inside code examples.

Both ADF (Jira Cloud) and plain text (Jira Server/older) formats are supported.

### Deduplication

Two-tier approach matching the GitHub integration pattern:

1. **In-memory BoundedSet**: Tracks processed comment IDs within a session (capped at 10,000 entries). Fast, but lost on restart.
2. **Persistent tracker**: `.jira-processed.json` in the instance directory. Loaded on startup, trimmed to 5,000 entries to prevent unbounded growth. Written via atomic file operations.

### Polling and backoff

| Condition | Check interval |
|-----------|---------------|
| Mentions found | `check_interval_seconds` (default: 60s) |
| 1 empty check | 2x base interval |
| 2 consecutive empty | 4x base interval |
| 3+ consecutive empty | `max_check_interval_seconds` cap (default: 180s) |

Backoff resets immediately when any mention is found.

## Jira Issue Context in Skills

When a mission originates from a Jira URL (e.g. `/fix https://myorg.atlassian.net/browse/FOO-123`), the skill runners (`/fix`, `/plan`, `/implement`) automatically detect the Jira URL and fetch full issue context from the Jira REST API:

- **Title**: Issue summary
- **Description**: Full issue body (converted from ADF to plain text)
- **All comments**: Every comment with author attribution (ADF to plain text)

This context is fed to Claude the same way GitHub issue context would be — the agent sees the complete Jira issue when working on the fix or plan.

Skills that accept GitHub issue/PR URLs also accept Jira browse URLs:
- `/fix https://myorg.atlassian.net/browse/FOO-123`
- `/plan https://myorg.atlassian.net/browse/FOO-123`
- `/implement https://myorg.atlassian.net/browse/FOO-123`

When the source is Jira, GitHub-specific steps (closed-state check, PR submission) are adjusted — PR submission still works if the Koan project has a `github_url` configured in `projects.yaml`.

## Security Model

### Authentication

Jira API calls use **HTTP Basic authentication** with your Atlassian account email and an API token. The token is never logged. It can be provided via:
- `KOAN_JIRA_API_TOKEN` environment variable (recommended)
- `jira.api_token` in config.yaml

### Permission checks

Every command goes through:

1. **Allowlist check**: The commenter's email must be in `authorized_users` (or wildcard `*` is set)
2. **Stale comment protection**: Comments older than `max_age_hours` are silently discarded

> **Note**: Unlike GitHub, Jira does not expose a "write access" check via its REST API. Permission control relies on the `authorized_users` allowlist. Use explicit email lists instead of `["*"]` for tighter security.

### Code block protection

@mentions inside Jira code blocks (`{code}...{code}`, `{{...}}`, `{noformat}...{noformat}`) are ignored, preventing accidental command triggers from code examples.

### JQL injection prevention

Jira project keys used in JQL queries are validated against a strict alphanumeric pattern (`^[A-Z0-9]+$`). Non-conforming keys are silently filtered out.

## Running Both Integrations

Jira and GitHub integrations are designed to coexist. They serve complementary roles:

| | GitHub | Jira |
|---|---|---|
| **Primary use** | Code-level actions (PR rebase, code review, implementation) | Issue tracking and project planning |
| **Trigger location** | PR/issue comments on GitHub | Issue comments on Jira |
| **Mission marker** | 📬 | 🎫 |
| **Auth method** | `gh` CLI + `GH_TOKEN` | HTTP Basic + API token |
| **Permission model** | Allowlist + GitHub write access check | Allowlist (email-based) |
| **Polling** | GitHub notifications API | JQL search + comment fetch |

### Combined configuration

```yaml
# GitHub integration
github:
  nickname: "koan-bot"
  commands_enabled: true
  authorized_users: ["*"]

# Jira integration
jira:
  enabled: true
  base_url: "https://myorg.atlassian.net"
  email: "bot@example.com"
  nickname: "koan-bot"
  authorized_users: ["*"]
  projects:
    PROJ: myproject              # Simple format
    INFRA:                       # Extended format with target branch
      project: infrastructure
      branch: "11.126"
```

```bash
# In .env
GH_TOKEN=ghp_xxxx
KOAN_JIRA_API_TOKEN=xxxx
```

Both integrations poll independently during the agent's sleep cycle — GitHub notifications are checked first, then Jira. Each has its own backoff schedule. Missions from both sources enter the same `missions.md` queue and are processed identically by the agent loop.

### When to use which

- **GitHub @mentions**: Best for code-centric actions — rebasing a PR, reviewing a diff, implementing a specific issue with linked code context.
- **Jira @mentions**: Best for project-level planning — turning a Jira epic into implementation tasks, planning a feature described in a ticket, auditing code related to a Jira story.

Both can trigger the same set of commands. The difference is the context URL attached to the mission — a GitHub URL gives the agent direct access to diffs and PR metadata, while a Jira URL provides issue descriptions and comment threads.

## Troubleshooting

### Commands not being picked up

1. **Check feature is enabled**: `jira.enabled: true` in config.yaml
2. **Verify required fields**: `base_url`, `email`, `api_token`, and `nickname` must all be set. Check logs for startup validation warnings.
3. **Check project mapping**: The Jira issue's project key must be in `jira.projects`. A comment on `FOO-123` requires `projects: { FOO: some_project }`.
4. **Check polling**: Look for `[jira]` log entries in `make logs`. If you see "no recently-updated issues found", the JQL query isn't matching.
5. **Verify API access**: Test manually:
   ```bash
   curl -X POST -u "email@example.com:YOUR_API_TOKEN" \
     -H "Content-Type: application/json" \
     "https://myorg.atlassian.net/rest/api/3/search/jql" \
     -d '{"jql": "project = FOO", "maxResults": 1}'
   ```
   > **Note**: Jira Cloud deprecated `GET /rest/api/3/search` (returns HTTP 410). Koan uses `POST /rest/api/3/search/jql` with cursor-based pagination.

### Mission queued but not executed

The 🎫 mission was written to `missions.md`. Check:
- `instance/missions.md` — the mission should be in the Pending section
- Agent loop logs — the mission will be picked up in the next iteration
- Project name resolution — the `repo:` override or project mapping must point to a valid Koan project in `projects.yaml`

### "No valid project keys after sanitization"

Jira project keys must be uppercase alphanumeric (e.g., `FOO`, `MYPROJ`). Keys with special characters are silently filtered out. Check your `jira.projects` mapping uses valid keys.

### Duplicate missions after restart

Expected behavior. The in-memory processed set is lost on restart, but the persistent tracker (`.jira-processed.json`) prevents most duplicates. If a crash occurred between mission creation and tracker update, a duplicate may appear — it's harmless and the agent handles already-completed missions gracefully.

## Related

- [GitHub Notification Commands](github-commands.md) — GitHub @mention integration (complementary)
- [Messaging: Telegram](messaging-telegram.md) — Primary command interface
- [Messaging: Slack](messaging-slack.md) — Alternative messaging provider
- [Skills Reference](skills.md) — Full skill documentation
- [User Manual](user-manual.md) — Complete usage guide
