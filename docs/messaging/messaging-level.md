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
| PR-producing tracked skill completion (`/review` `/fix` `/rebase` `/implement`) | one short outcome line: `✅ [project] 🔍 Reviewed <pr-url>` (emitted by the agent loop; the runner's own outcome line is suppressed to avoid a duplicate row) | progress + verbose summary (both lines) |
| `/plan` completion | one outcome line from the **runner** carrying the issue/Jira URL or inline plan body (`✅ Plan created: <url>` / `✅ Plan generated inline:\n\n<body>`); the agent loop's bare `🧠 Planned` line is logged only | progress + verbose summary |
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

  Exception: for the **PR-producing tracked** skills (`/review`, `/fix`,
  `/rebase`, `/implement`) dispatched through the agent loop, the loop already
  emits a canonical completion line (`✅ [project] 🔍 Reviewed <pr-url>`) that
  carries the same PR URL. To avoid two rows advertising the same result, the
  agent loop sets `KOAN_SUPPRESS_RUNNER_OUTCOME=1` for the runner subprocess in
  `normal` mode, so the runner's own success outcome line is logged only (not
  sent). Failure outcome lines are still sent — the agent-loop replacement carries
  only the mission title, so the runner's specific failure reason must still reach
  chat. `debug` keeps both.

  `/plan` is **not** suppressed: it never opens a PR, so the canonical line
  (`✅ [project] 🧠 Planned`) cannot carry its result URL. The PR-only extraction
  that builds the canonical line never matches a `/plan` outcome — which is an
  *issue*/Jira URL (`✅ Plan created: <url>`, `✅ Plan posted as comment on …`) or
  an inline plan body (`✅ Plan generated inline:\n\n<body>`). Suppressing the
  runner here would drop that content entirely, so the runner's line is left to
  reach chat and the agent loop logs its bare `🧠 Planned` line instead of sending
  it (avoiding the duplicate from the other direction). `notify_outcome` keeps a
  defensive single-line guard so only bare `✅` restatements are ever suppressed.

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
