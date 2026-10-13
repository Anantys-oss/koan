---
type: doc
title: "Messaging level (bridge verbosity)"
description: "Explains the `messaging.level` setting (`normal`/`debug`) that controls how much lifecycle/progress chatter Kōan's Telegram/Slack bridge sends versus only logs."
tags: [messaging]
created: 2026-06-24
updated: 2026-10-13
---

# Messaging level (bridge verbosity)

Kōan's Telegram/Slack bridge can be **chatty** — historically it announced every
mission start, every per-mention queue event, and every autonomous run. The
`messaging.level` setting controls this firehose with two values:

| Level | Behavior |
|-------|----------|
| `normal` (default) | Quiet, operator-focused. Failures, command replies, and one-line PR results still come through. Per-mention queue lines collapse into a single aggregate count; mission-start `🚀` lines and autonomous-run successes are suppressed. |
| `debug` | Full lifecycle narration (the legacy firehose). Every per-mention line, mission start, and verbose completion summary is sent. |

**Every suppressed message is still written to the logs.** Nothing is lost for
debugging — `normal` only changes what reaches the bridge, not what is recorded.

## Configuration

Config key in `instance/config.yaml`:

```yaml
messaging:
  level: normal   # one of: debug, normal
```

### Precedence

Resolved highest-priority first:

1. `KOAN_MESSAGING_LEVEL` environment variable
2. `.koan-messaging-level` runtime state file (written by `/messaging_level`)
3. `messaging.level` in `config.yaml`
4. `"normal"` (default)

Unknown values (typos like `verbose`) coerce to `normal`; resolution never raises.

## The `/messaging_level` skill

| Command | Aliases | Description |
|---------|---------|-------------|
| `/messaging_level` | `/msglevel` | Show or set bridge verbosity |

- `/messaging_level` — show the active level.
- `/messaging_level debug` — restore the full firehose (writes the state file).
- `/messaging_level normal` — return to quiet mode.

The skill writes the `.koan-messaging-level` state file, overriding `config.yaml`
without rewriting YAML — handy for temporary debugging.

## What each level shows

| Event | `normal` | `debug` |
|-------|----------|---------|
| Mission start (`🚀 … Starting/Autonomous/Skill`) | log only | sent |
| Skill-runner **progress** (`Reviewing PR…`, `Analyzing code changes…`, `Posting review…`, `🧠 Planning…`, `🔍 Checking…`) | log only | sent |
| Tracked skill completion (`/review` `/ultrareview` `/fix` `/rebase` `/plan` `/implement` `/squash` `/recreate` `/debug` `/speckit`) | one message from the agent loop: `✅ [project] 🔍 Reviewed <url>`, with the runner's detail (gate note, plan body, warning) on the lines below | the runner's own message + the loop's message with run counter and journal summary |
| Operator-initiated mission success (a user/Telegram-queued task with a real title) | one short line: `✅ [project] Done: <title>` | sent with journal summary |
| Autonomous-run success (no mission title) | log only | sent with journal summary |
| Mission failure | one message: `❌ [project] 🔍 Review failed: <pr-url>` + the reason on the next line (`❌ [project] Failed: <title>` for other missions) | same first line + run counter + failure context |
| GitHub/Jira per-mention queue line | log only | sent |
| GitHub/Jira queued aggregate | `📬 GitHub: N new missions queued.` (when N > 0) | not emitted (per-mention lines already shown) |
| GitHub notification/dispatch banners (`Processing N notification(s)…`) | log only | sent |
| Command replies | always | always |

### Progress vs. outcome (skill runners)

Skill runners (`/review`, `/rebase`, `/recreate`, `/squash`, `/checkup`, `/plan`,
`/check`, `/ai`, CI fixes) emit two kinds of message:

- **Progress** — intermediate step narration (`Reviewing PR #2098…`, `Analyzing
  code changes…`, `Posting review…`). Routed through a debug-gated notifier:
  always logged, forwarded to chat **only** in `debug`.
- **Outcome** — exactly one message per mission. Its first line is a **fixed
  template, sent verbatim** (it skips the Claude personality reformatter that
  rewrites other bot messages), so the same outcome always reads the same:

  ```
  ✅ [project] 🔍 Reviewed https://github.com/o/r/pull/2098
  ❌ [project] 🔄 Rebase failed: https://github.com/o/r/pull/7
  <reason>
  ```

  Full URL, no trailing full stop. Free text — a review-gate note, a plan body,
  a "feedback NOT applied" warning, a failure reason — goes only on the lines
  below. A failure starts with ❌ (🚦 for CI missions). Always sent, regardless of
  `messaging.level`.

  | Skill | Success | Failure |
  |---|---|---|
  | `/review` | `🔍 Reviewed <url>` | `🔍 Review failed: <url>` |
  | `/ultrareview` | `🔭 Ultra reviewed <url>` | `🔭 Ultra review failed: <url>` |
  | `/fix` | `🐞 Fixed <url>` | `🐞 Fix failed: <url>` |
  | `/rebase` | `🔄 Rebased <url>` | `🔄 Rebase failed: <url>` |
  | `/plan` | `🧠 Planned <issue-url>` | `🧠 Plan failed: <url>` |
  | `/implement` | `🔨 Implemented <url>` | `🔨 Implement failed: <url>` |
  | `/squash` | `🗜️ Squashed <url>` | `🗜️ Squash failed: <url>` |
  | `/recreate` | `♻️ Recreated <new-url>` | `♻️ Recreate failed: <url>` |
  | `/debug` | `🔬 Debugged <url>` | `🔬 Debug failed: <url>` |
  | `/speckit` | `📐 Specified <url>` | `📐 Speckit failed: <url>` |

  Other missions get `✅ [project] Done: <title>` / `❌ [project] Failed: <title>`.

  These skills' runners end with `messaging_level.report_outcome()`. Dispatched
  through the agent loop in `normal` mode (`KOAN_SUPPRESS_RUNNER_OUTCOME=1`), the
  runner does not send: it prints its outcome on stdout as `KOAN_OUTCOME_OK:` /
  `KOAN_OUTCOME_URL:` / `KOAN_OUTCOME_DETAIL:` / `KOAN_OUTCOME_REASON:` lines,
  and the agent loop sends the one message (`No reason given.` if the runner died
  before reporting). A skipped run (PR already merged) says so on the line below. `debug` keeps the runner's own message too. A runner's
  `main()` reports a failed run whose path did not, with its summary as the
  reason. The URL, detail and reason are secret-redacted; the reason is one
  line, capped at 500 characters.

So under `normal` a PR review produces a single chat line — the PR URL — instead
of the four-line play-by-play. Switch to `debug` to see every step again; nothing
was lost, the suppressed progress is in the log.

## One-time notice

On the first startup after upgrading, if `messaging.level` is **not** explicitly
set in `config.yaml`, Kōan sends a single advisory that the bridge defaults to
`normal` and how to restore the firehose. The notice fires once (tracked by an
`instance/.messaging-level-notice-sent` sentinel) and is skipped entirely when
the operator has explicitly chosen a level.

## Relationship to other settings

- **`notifications.min_priority`** filters per-message *severity* (urgent/action/
  warning/info). `messaging.level` is a separate *verbosity tier* for lifecycle
  chatter — the two compose.
- **`/verbose`** toggles in-mission progress narration, not global bridge
  verbosity. `messaging.level` governs the bridge as a whole.
