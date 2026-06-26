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
| Tracked skill completion (`/review` `/fix` `/rebase` `/plan` `/implement`) | one short outcome line: `✅ [project] 🔍 Reviewed <pr-url>` (emitted by the agent loop; the runner's own outcome line is suppressed to avoid a duplicate row) | progress + verbose summary (both lines) |
| Operator-initiated mission success (a user/Telegram-queued task with a real title) | one short line: `✅ [project] Done: <title>` | sent with journal summary |
| Autonomous-run success (no mission title) | log only | sent with journal summary |
| Mission failure | sent (short form) | sent with failure context |
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
- **Outcome** — exactly one terminal line per mission carrying the PR/issue URL
  on success (`✅ Reviewed https://github.com/o/r/pull/2098`) or a short context
  string on failure (`❌ Rebase failed https://github.com/o/r/pull/7: <reason>`).
  Always logged **and** sent, regardless of `messaging.level`.

  Exception: for **tracked** skills (`/review`, `/fix`, `/rebase`, `/plan`,
  `/implement`) dispatched through the agent loop, the loop already emits a
  canonical completion line (`✅ [project] 🔍 Reviewed <pr-url>`). To avoid two
  rows advertising the same result, the agent loop sets
  `KOAN_SUPPRESS_RUNNER_OUTCOME=1` for the runner subprocess in `normal` mode, so
  the runner's own **single-line** success outcome line is logged only (not sent).
  A **multi-line** success outcome is always sent — it carries content the
  canonical line does not (e.g. `/plan` with no tracker emits the full plan body
  in `✅ Plan generated inline:\n\n<body>`, the only place the plan reaches the
  user). Failure outcome lines are still sent too — the agent-loop replacement
  carries only the mission title, so the runner's specific failure reason must
  still reach chat. `debug` keeps both.

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
