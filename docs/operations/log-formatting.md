---
type: doc
title: "make logs formatting"
description: "Documents the display-side [cli] log formatter (log_fmt.py) behind make logs, its glyph legend, tick-collapse behavior, and the raw=1 escape hatch."
tags: [operations, observability, logs]
created: 2026-07-13
updated: 2026-07-13
---

# Pretty `make logs`

`make logs` tails `logs/run.log`, `logs/awake.log`, `logs/ollama.log`, and
`instance/journal/pending.md`, piping the `[cli] …` provider-summary lines
through a **display-only** formatter (`koan/app/log_fmt.py`). It is purely
presentational — the log **files** and the `[cli]` grammar emitted by
`app.provider.__init__._summarize_stream_event()` (parsed by `run.py`,
`quota_handler.py`, `jira_outcome_publish.py`, `claude_step.py`) are untouched.

## Glyph legend

| Raw `[cli]` line                    | Shown as        |
|-------------------------------------|-----------------|
| `assistant — thinking`, `system: thinking_tokens` | dim `•` (consecutive collapse) |
| `assistant — text: <preview>`       | `🧠 <preview>`   |
| `assistant — tool_use: Edit`        | `✏️ Edit` (per-tool icons; `🔧` default) |
| `tool_result …`                     | dim `↩`         |
| `tool_result … (error)`             | `❌ tool error` (high-signal; never collapses) |
| `result: success (12s)`             | `✅ result: success (12s)` |
| `retry …`, `context_overflow …`, `rate_limit_rejected …` | `⚠ …` |

Non-`[cli]` lines (run.py lifecycle, awake.py, pending.md, `tail` headers) and
any unrecognized `[cli]` shape pass through verbatim. Color is emitted only to a
TTY (`KOAN_FORCE_COLOR` forces it; `NO_COLOR` disables it).

## Raw escape hatch

```bash
make logs raw=1   # skip the formatter, show the exact bytes
```

## Maintenance note

The formatter matches the `[cli] ` string grammar. If you change
`_summarize_stream_event()`'s output, update `koan/app/log_fmt.py` and
`koan/tests/test_log_fmt.py` in the same change.
