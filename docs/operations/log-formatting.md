---
type: doc
title: "make logs formatting"
description: "Documents the display-side [cli] log formatter (log_fmt.py) behind make logs, its glyph legend, tool-input previews, accumulating thinking dots, and the raw=1 escape hatch."
tags: [operations, observability, logs]
created: 2026-07-13
updated: 2026-07-14
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
| `assistant — thinking`, `system: thinking_tokens` | dim `•`, one per event, **accumulated** into a growing run (`••••`) — rewritten in place on a TTY, emitted as one `•×N` line when piped |
| `assistant — text: <preview>`       | `🧠 <preview>` (preview skips leading code fences / bare brackets, e.g. ```` ```json ```` → first real line) |
| `assistant — tool_use: Bash: <cmd>` | `💻 Bash <cmd…>` (per-tool icon + dim, first-line input preview: command / file path / pattern / url, truncated with `…`; `🔧` default) |
| `tool_result …`                     | *(suppressed — adds no signal)* |
| `tool_result … (error)`             | `❌ tool error` (high-signal; never suppressed) |
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

The tool-input preview comes from `_summarize_stream_event()`'s
`tool_use: <name>: <preview>` form. Changing the input-key priority list
(`_TOOL_PREVIEW_KEYS` in `koan/app/provider/__init__.py`) or the summary grammar
requires updating `log_fmt.py`, `koan/tests/test_log_fmt.py`, and
`koan/tests/test_provider_modules.py` together.
