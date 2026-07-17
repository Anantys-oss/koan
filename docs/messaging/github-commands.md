---
type: doc
title: "GitHub Notification-Driven Commands"
description: "Full reference for triggering Kōan via `@mention` commands in GitHub PR/issue comments, including config, dedup, security, and fallback scanning."
tags: [messaging]
created: 2026-05-28
updated: 2026-07-13
---

# GitHub Notification-Driven Commands

Control Kōan directly from GitHub PR and issue comments using `@mention` commands.

> **Introduced in**: [PR #251](https://github.com/Anantys-oss/koan/pull/251) — 10 commits, 6 new modules, 102 tests.

## Overview

Instead of switching to Telegram to tell Kōan to rebase a PR or review an issue, you can post a comment on the PR/issue itself:

```
@koan-bot rebase
```

Kōan polls GitHub notifications, detects the `@mention`, validates the command and the user's permissions, reacts with 👍 to acknowledge, and queues a mission — all without webhooks or external services.

GitHub can occasionally record a mention in a PR timeline without returning a
matching notification thread to the bot account. To cover that gap, Kōan also
runs a bounded fallback scan over recent comments in configured repositories.
The fallback uses the same permission checks, mission creation, reaction
acknowledgement, and deduplication tracker as the notification path.

## Quick Start

### 1. Enable the feature

In `instance/config.yaml`:

```yaml
notification_polling:
  check_interval_seconds: 60       # Base polling interval shared by GitHub/Jira
  max_check_interval_seconds: 300  # Quiet-period backoff cap

github:
  nickname: "koan-bot"          # Your bot's GitHub username (required)
  commands_enabled: true         # Master switch
  authorized_users: ["*"]        # "*" = anyone with write access, or ["alice", "bob"]
  max_age_hours: 24              # Ignore notifications older than this (default: 24)
  mention_scan_interval_minutes: 5  # Fallback scan for mentions missing from notifications
```

### 2. Make sure `gh` is authenticated

Kōan uses the `gh` CLI for all GitHub API calls. Verify it works:

```bash
gh auth status
gh api notifications --paginate | head
```

### 3. Post a command in a PR/issue comment

```
@koan-bot rebase
```

Kōan will:
1. React with 👍 on the comment (acknowledgment)
2. Create a pending mission: `- [project:myapp] /rebase https://github.com/owner/repo/pull/42`
3. Execute it in the next agent loop iteration

## Available Commands

Any skill with `github_enabled: true` in its `SKILL.md` can be triggered via @mentions. Currently **16 commands** are available:

| Command | Aliases | What it does | Context-aware |
|---------|---------|--------------|---------------|
| `ask` | — | Ask Koan a question about a PR or issue | **Yes** |
| `audit` | — | Audit a project codebase and create issues for findings | **Yes** |
| `brainstorm` | — | Decompose a topic into linked GitHub issues | **Yes** |
| `deepplan` | `deeplan` | Spec-first design with Socratic exploration | **Yes** |
| `fix` | — | Fix a GitHub issue end-to-end, or batch-queue all open issues | **Yes** |
| `gh_request` | — | Natural-language GitHub request dispatch | **Yes** |
| `implement` | `impl` | Implement a GitHub issue | **Yes** |
| `plan` | — | Deep-think and create a structured plan | **Yes** |
| `profile` | `perf`, `benchmark` | Queue a performance profiling mission | **Yes** |
| `rebase` | `rb` | Rebase a PR onto latest upstream | **Yes** |
| `recreate` | `rc` | Recreate a diverged PR from scratch | **Yes** |
| `refactor` | `rf` | Queue a refactoring mission | **Yes** |
| `review` | `rv` | Queue a code review for a PR or issue | **Yes** |
| `reviewrebase` | `rr` | Review then rebase combo for a PR | **Yes** |
| `security_audit` | `security`, `secu` | Security-focused audit of a codebase | **Yes** |
| `squash` | `sq` | Squash all PR commits into one clean commit | **Yes** |

### Context-aware commands

Some commands accept additional context after the command word. For example:

```
@koan-bot implement phase 1 only
```

This creates a mission: `/implement https://github.com/owner/repo/issues/42 phase 1 only`

Only skills with `github_context_aware: true` in their `SKILL.md` receive the extra context. For other commands, trailing text is ignored.

### Using a URL in the context

If the context contains a GitHub URL, it overrides the default subject URL from the notification:

```
@koan-bot implement https://github.com/owner/other-repo/issues/99 phase 2
```

## Natural-language intent ladder

With `github.natural_language: true`, a mention whose first word isn't a
command (e.g. `@koan-bot eh do a review`) is resolved via a three-layer ladder
before any free-form fallback:

1. **Keyword** — a whole-word scan of the first few tokens after the @mention
   against github-enabled skill names + aliases. Exactly one distinct match in
   an *actionable position* — token 0, or preceded by an imperative lead-in
   (`do a review`, `can you rebase`) — is promoted straight to that skill
   (`/review <url> …`), with the same handlers, acks, and URL guards as a rigid
   command. An incidental noun use (`the review looks good`) is not treated as
   intent; it escalates to the model layer instead of auto-dispatching.
2. **Model** — if the keyword layer is ambiguous (zero or several matches, or a
   non-actionable position), the
   lightweight classifier picks the single best command and a confidence score.
   It is promoted only at/above `min_confidence` and when the command's required
   URL type matches the subject (a PR command on an Issue is rejected).
3. **Free-form** — genuinely ambiguous prose keeps the `/gh_request`
   compatibility route (which reuses the same classifier). A mention is never
   dropped.

Primary routing happens at the bridge. `/gh_request` remains for explicit
invocation and as the free-form fallback for prose that neither layer resolved.

Config (defaults shown):

```yaml
github:
  natural_language: true
  intent:
    keyword_window: 5      # tokens after @mention scanned for a skill keyword
    min_confidence: 0.75   # model promotes only at/above this certainty (0.0–1.0)
```

## Configuration

### Global settings (`instance/config.yaml`)

```yaml
github:
  nickname: "koan-bot"          # Bot's GitHub @mention name (required if enabled)
  commands_enabled: false        # Master switch (default: false)
  authorized_users: ["*"]        # Allowlist: "*" for all with write access, or explicit usernames
  max_age_hours: 24              # Stale notification threshold (default: 24 hours)
  mention_scan_interval_minutes: 5  # Fallback scan interval for configured repos
```

- **`nickname`**: The GitHub username Kōan uses. Must match the account behind `GH_TOKEN`. This is the `@name` users will mention.
- **`commands_enabled`**: Feature toggle. When `false`, notification polling is completely skipped.
- **`authorized_users`**: Controls who can trigger commands. Even with `["*"]`, Kōan always verifies the user has **write access** to the repository via the GitHub API. This prevents drive-by command injection from random commenters.
- **`max_age_hours`**: Notifications and fallback-scanned comments older than this are silently discarded. Protects against processing a backlog of stale mentions after downtime.
- **`mention_scan_interval_minutes`**: Minimum interval between fallback comment scans for the same configured repo. Defaults to 5 minutes; set `0` to scan on every GitHub poll.

#### AI reply settings

When `reply_enabled: true`, Kōan responds to non-command @mentions with AI-generated replies. Two additional settings control who can trigger replies and how often:

```yaml
github:
  reply_enabled: true
  reply_authorized_users: ["*"]    # Who can trigger AI replies (default: uses authorized_users)
  reply_rate_limit: 5              # Max replies per user per hour (default: 5, min: 1)
```

- **`reply_authorized_users`**: Separate from command `authorized_users` — allows a broader audience for read-only replies without granting command execution. `["*"]` means anyone can trigger replies (no permission check at all, unlike command wildcard which still checks GitHub write access). Omit to fall back to `authorized_users`. Set `[]` to disable replies entirely.
- **`reply_rate_limit`**: Prevents API quota abuse when replies are open broadly. Tracks per-user reply counts over a rolling 1-hour window. Default: 5, minimum: 1.

#### Command acknowledgment

When a command is dispatched from a GitHub @mention, Kōan can post a brief acknowledgment reply (e.g. "🤖 `/review` queued — I'll get to it shortly.") so the user knows their request was received. Replies are threaded: PR review comments get native GitHub threading, issue comments include a blockquote of the original message.

Acknowledgment replies are **off by default** — the emoji reaction Kōan places on the comment already signals receipt, and the extra reply can be noisy. Opt in by setting `ack_enabled: true`:

```yaml
github:
  ack_enabled: true   # Post acknowledgment replies (default: false)
```

When disabled (the default), reactions are still placed.

#### Reply circuit breaker

To prevent a runaway reply loop from spamming a thread (e.g. a misconfiguration or a future regression that keeps re-discovering the same comments), Kōan caps the number of bot comments it will post to a single PR/issue thread within a rolling hour:

```yaml
github:
  max_replies_per_thread_per_hour: 10   # default: 10; set 0 to disable
```

Once the cap is reached, further acks/errors/replies on that thread are suppressed (logged, with a single Telegram heads-up to the operator) until the rolling window clears. The counter is persisted under `instance/`, so the breaker survives restarts.

#### Reply threading

All AI-generated replies (from `reply_enabled`, `/ask`, and command acknowledgments) are threaded to the original comment:

- **PR review comments** (`#discussion_r` URLs): replies use GitHub's native `in_reply_to` threading.
- **Issue/PR comments** (`#issuecomment-` URLs): replies include a `> @user: ...` blockquote for visual context.

#### Multiple instances

When several Kōan instances share the same GitHub account (each watching a different set of repos), @mentions on repos not in this instance's `projects.yaml` trigger warnings by default. Suppress them with:

```yaml
enable_multiple_instances: true
```

This is a top-level config key (not nested under `github:`). When enabled, Kōan silently skips @mentions from unregistered repos instead of logging warnings and sending Telegram alerts — the assumption is that another instance handles them. Notifications from unregistered repos are left unread so sibling instances can process them.

When `enable_multiple_instances` is **false** (default, single-instance mode), notifications from unregistered repos are automatically marked as read to prevent inbox accumulation — no other instance will claim them.

The @mention-dropped warning is one-shot per repo for the life of the process (it will not repeat every poll cycle). It resets once you add the repo to `projects.yaml`: the next time an @mention arrives it is acted on normally, and if the repo is ever removed again a fresh warning is emitted. Restarting Kōan also re-arms the warning as a standing reminder that the repo is still not registered.

### Per-project overrides (`projects.yaml`)

Override `authorized_users` and `reply_authorized_users` for specific repositories:

```yaml
projects:
  sensitive-repo:
    path: "/path/to/sensitive-repo"
    github:
      authorized_users: ["alice", "bob"]  # Only these users, not the global wildcard
      reply_authorized_users: ["*"]       # But allow AI replies for anyone
```

This is useful when the global config allows `["*"]` but a specific repo needs tighter control for commands, or vice versa for replies.

### Environment variables

| Variable | Purpose |
|----------|---------|
| `GH_TOKEN` | GitHub authentication for the `gh` CLI (required) |
| `GITHUB_USER` | Override bot username for API calls (optional, falls back to `github.nickname`) |

## Running indicator (`koan/mission`)

While Kōan works a GitHub-linked mission, it surfaces a live "Running" signal
directly on GitHub — **no GitHub App or Action required**, it reuses the same
`gh` auth as everything else:

- **Issue label** `koan:working` — added to the linked issue at mission start
  and removed when the mission reaches a terminal state. This is the **primary
  live signal**, because the issue is already known when the mission starts.
- **Commit status** `context=koan/mission` — posted `pending` on the pushed
  branch head at first push, then resolved to `success` / `failure` / `error`
  when the mission finishes. This is the durable green/red on the PR.

It is **on by default** and best-effort: a `gh` write failure (e.g. a PAT
missing `repo:status` scope) is logged and never blocks the mission. It is a
**no-op for local-only missions** — those with no issue URL in the mission text
and no `github_url` configured for the project write nothing.

Every terminal path tears the indicator down — normal completion, failure,
abort, and stagnation-cap. A hard crash that skips teardown is reconciled at
the next `run.py` startup (any stranded `pending` becomes `error` and the label
is removed).

### Global settings (`instance/config.yaml`)

```yaml
running_indicator:
  enabled: true            # Master switch (default: true). Set false to opt out.
  commit_status: true      # Post the koan/mission commit status (default: true)
  issue_label: true        # Toggle the koan:working issue label (default: true)
  label_name: "koan:working"  # Label text (default: koan:working)
```

A bare bool is also accepted as shorthand for `enabled`:

```yaml
running_indicator: false   # equivalent to { enabled: false }
```

### Per-project override (`projects.yaml`)

Any subset of the global keys can be overridden per project; unset keys inherit
the global values:

```yaml
projects:
  my-toolkit:
    path: "/path/to/my-toolkit"
    running_indicator:
      enabled: false       # disable the indicator for just this project
```

See also the sibling Telegram progress channel in
[messaging-level.md](messaging-level.md), and the flow/tracker detail in
[../architecture/github-and-trackers.md](../architecture/github-and-trackers.md).

## How It Works

### Architecture

The feature spans 6 modules in `koan/app/`:

```
loop_manager.py           ← Polls during sleep cycle (throttled)
  ↓
github_notifications.py   ← Fetches & filters notifications, parses @mentions
  ↓
github_command_handler.py ← Validates commands, scans requested reviews, creates missions
  ↓
github_config.py          ← Reads config.yaml / projects.yaml settings
  ↓
github_skill_helpers.py   ← Shared URL extraction, project resolution, mission queuing
  ↓
skills.py                 ← Skill flags: github_enabled, github_context_aware
```

### Notification processing flow

```
1. Sleep cycle tick → process_github_notifications()
2. Fetch notifications (including recently read notifications in the configured lookback, filtered to known repos)
3. For each notification:
   a. Skip if stale (> max_age_hours)
   b. Fetch triggering comment
   c. Skip if self-mention (bot's own comments)
   d. Check in-memory + reaction-based deduplication
   e. Parse @mention → extract (command, context)
   f. Validate command → skill must have github_enabled: true
   g. Check user permission → allowlist + GitHub write access
   h. Insert mission into missions.md (BEFORE reacting — crash-safe)
   i. React with 👍 on comment (marks as processed)
   j. Mark notification thread as read
4. Independently scan recent comments in configured repos for unprocessed `@nickname` mentions that GitHub did not expose via notifications
5. Scan known repos for open non-draft PRs that still request the bot as reviewer
6. Queue `/review <pr-url>` for requested reviews that do not already have an active mission
```

### Deduplication strategy

Two-tier approach to prevent duplicate missions:

1. **In-memory set**: `_processed_comments` tracks comment IDs within a session. Fast, but lost on restart.
2. **GitHub 👍 reaction**: Persistent marker. On restart, Kōan checks if it already reacted before processing.

The mission is inserted **before** the reaction is added. If Kōan crashes between these two steps, the worst case is a duplicate mission — never a lost command.

Because a single notification thread is rescanned for **all** unprocessed @mentions on every poll, *every* comment Kōan acts on — whether it queued a mission, posted an error, denied permission, or sent help — is also recorded in the persistent comment tracker (`instance/.koan-github-processed.json`). The reaction alone is volatile (it depends on the reactions API and a correctly-configured `bot_username`); the local tracker is the durable backstop that guarantees a comment is answered **at most once** and never re-discovered on a later poll. This is what prevents the same error/help reply from being re-posted on every cycle.

Review-request notifications have an additional backstop: the agent scans
configured `projects.yaml` repositories for open PRs whose requested reviewers
include `github.nickname`. This catches GitHub cases where the PR timeline shows
a review request but the notifications API returns no matching notification.
Those scan results are deduplicated by `owner/repo#pr` plus the PR head SHA, so
new commits can trigger a fresh review while repeated polls of the same commit
do not. Dedup against existing missions matches the **exact** PR URL token, so
PR #42 is never mistaken for PR #421.

To bound the GitHub API cost, the scan is **throttled per repository**: each
repo is polled with `gh pr list` at most once per
`github.review_scan_interval_minutes` (default `15`; set `0` to scan every
cycle). Across configured repos the per-repo `gh` calls run **concurrently**
(bounded by `github.parallel_workers`, default `4`, ceiling `16`); the resulting
missions are written serially so `missions.md` ordering stays deterministic. A
repo whose fetch fails (SSO, timeout) is not marked scanned and is retried on
the next poll.

### Polling & backoff

Notifications are checked during the agent's interruptible sleep cycle, with exponential backoff:

| Condition | Check interval |
|-----------|---------------|
| Notifications found | `check_interval_seconds` (default: 60s) |
| 1 empty check | 2x base interval |
| 2 consecutive empty | 4x base interval |
| 3+ consecutive empty | `max_check_interval_seconds` cap (default: 300s) |

Backoff resets immediately when any notification is found. The recommended
shared setting is `notification_polling.max_check_interval_seconds: 300`, which
lets always-on instances stay ready without polling GitHub more than once every
five minutes during quiet periods. Legacy `github.check_interval_seconds` and
`github.max_check_interval_seconds` settings still work as GitHub-only
overrides. If `auto_pause` is disabled and there is no work, the agent loop also
parks on this backoff so it does not flood logs while waiting for the next poll.

> **Want faster responses?** The polling delay can be collapsed to a few seconds with the opt-in **push-based webhook receiver** — see [docs/messaging/github-webhooks.md](github-webhooks.md). Webhooks trigger an immediate poll; polling stays on as the reliability fallback.

### Error handling

When a command fails validation (unknown command, permission denied), Kōan:
1. Posts an error reply on the GitHub comment thread (❌ with explanation)
2. Includes the list of available commands for "unknown command" errors
3. Deduplicates error replies to avoid spam, and marks the triggering comment processed so it is never re-answered on a later poll
4. Honours the per-thread reply circuit breaker (`max_replies_per_thread_per_hour`) — once tripped, error replies are suppressed

### Closed / merged subjects

Commands are meaningless on a closed or merged PR/issue, so Kōan never posts acks, errors, or replies on them. The closed-subject check is enforced on **every** reply path — both the main notification handler and the error-reply fallback — and the affected comments are reacted to (👀) and marked processed so the closed thread is not re-scanned every poll. A single Telegram notice is sent the first time a closed subject is skipped.

### Code block protection

`@mentions` inside code blocks are ignored:

````markdown
Here's an example:
```
@koan-bot rebase  ← This is NOT processed
```

@koan-bot rebase  ← This IS processed
````

## Adding GitHub Support to a Custom Skill

Any skill can opt into GitHub @mention triggering by adding flags to its `SKILL.md`:

```yaml
---
name: my-skill
github_enabled: true              # Allow triggering via @mentions (also enables Jira)
github_context_aware: true        # Pass extra text as context (optional)
group: integrations               # Groups the skill under "Integrations" in help
commands:
  - name: my-command
    description: "Does something useful"
handler: handler.py
---
```

The skill's handler receives the same `SkillContext` whether triggered from Telegram, GitHub, or Jira. The mission format for core skills is `/my-command <url> [context]`.

### In-process dispatch for custom skills

Skills under `instance/skills/<scope>/` with a `handler.py` follow a shorter path: the GitHub/Jira bridges call `execute_skill(skill, ctx)` directly at notification time — the same entry point Telegram uses — instead of queueing a slash mission that has no registered runner in `skill_dispatch._SKILL_RUNNERS`. This keeps custom skills self-contained: the handler can queue whatever mission it needs via `insert_pending_mission`.

The helper is `app.external_skill_dispatch.try_dispatch_custom_handler`. It also **auto-feeds a Jira key** into `ctx.args` when the author omitted one:

- **Jira source**: the issue the comment is on.
- **GitHub source**: the first `FOO-123`-style key found in the issue title, then body.
- If the author already typed a key (e.g. `@bot myfix PROJ-1`), it's passed through verbatim.

### Help grouping: the `integrations` group

Non-core skills should set `group: integrations` so they render in a dedicated **Integrations** section at the bottom of `@bot help`, separate from the core command groups (code, pr, missions, …).

See [koan/skills/README.md](../../koan/skills/README.md) for the full skill authoring guide.

## Security Model

### Permission checks

Every command goes through two gates:

1. **Allowlist check**: User must be in `authorized_users` (or wildcard `*` is set)
2. **Write access verification**: Even with wildcard auth, Kōan always calls the GitHub API to verify the user has `write` or `admin` permission on the repository

This means a random person commenting `@koan-bot rebase` on a public repo will be rejected — they need actual write access, not just the ability to comment.

### Stale notification protection

Notifications older than `max_age_hours` (default: 24h) are silently discarded and marked as read. This prevents processing an accumulated backlog after extended downtime.

### Self-mention filtering

Comments posted by the bot itself are always ignored, preventing infinite loops.

### Mission-first ordering

The mission is written to `missions.md` before the 👍 reaction is added. This guarantees:
- **No lost commands**: If Kōan crashes after writing the mission but before reacting, the mission persists. On restart, it will re-process the notification but find the mission already exists.
- **At-most-once reaction**: The reaction serves as a durable "processed" marker.

## Troubleshooting

### Commands not being picked up

1. **Check feature is enabled**: `commands_enabled: true` in config.yaml
2. **Verify nickname matches**: `github.nickname` must match the GitHub account behind `GH_TOKEN`
3. **Check notification visibility**: `gh api notifications --paginate` should show the mention
4. **Check logs**: `make logs` — look for `GitHub:` log entries
5. **Verify write access**: The commenting user needs write/admin permission on the repo

### Bot reacts but doesn't execute

The 👍 means Kōan acknowledged the command and created a mission. Check:
- `instance/missions.md` — the mission should be in the Pending section
- Agent loop logs — the mission will be picked up in the next iteration

### "Unknown repository" error

The repo must be configured in `projects.yaml` with a valid `path`. Kōan resolves the notification's repository against known projects. If there's no match, it can't determine where to execute.

### Duplicate missions after restart

Expected behavior when Kōan was interrupted between mission creation and reaction. The duplicate will be harmless — the agent detects already-completed missions.

## Co-existence with Jira

GitHub and Jira integrations can run simultaneously. Both dispatch the same set of commands (any skill with `github_enabled: true`) but serve different roles:

- **GitHub**: Code-centric actions — PR rebases, code reviews, issue implementation with direct diff access.
- **Jira**: Project-level planning — feature planning, audits, and implementation from Jira tickets.

Missions from GitHub are marked with 📬, missions from Jira with 🎫. Both enter the same mission queue.

Per-project issue routing is configured in `projects.yaml` under `issue_tracker`. Use `/tracker` from Telegram to inspect or update whether a project creates new tracker issues in GitHub or Jira.

See [Jira Integration](jira-integration.md) for full setup instructions and the combined configuration guide.

## Related

- [Jira Integration](jira-integration.md) — Jira @mention integration (complementary)
- [Skills README](../../koan/skills/README.md) — Skill authoring guide with `github_enabled` flag documentation
- [Messaging: Telegram](telegram.md) — Alternative command interface via Telegram
- [Messaging: Slack](slack.md) — Alternative command interface via Slack
- [Messaging: Matrix](matrix.md) — Alternative command interface via Matrix
- [PR #251](https://github.com/Anantys-oss/koan/pull/251) — Original implementation
- [Issue #243](https://github.com/Anantys-oss/koan/issues/243) — Feature request and design plan
