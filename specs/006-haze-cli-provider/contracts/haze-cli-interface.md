# External Contract: haze headless CLI (≥ 0.7.0)

What Kōan depends on from the haze binary. Source of truth: haze `src/cli/commands/runCommand.ts` + CHANGELOG 0.7.0 (verified 2026-07-10 against v0.8.0). If haze changes any row, the provider must be revisited.

## Invocation

| Aspect | Contract |
|---|---|
| Headless trigger | `-p/--prompt <text>`, **or** stdin when `-p` absent and stdin is not a TTY |
| Model override | `-m/--model <selector>` (`provider:name` or unambiguous bare name); per-run, never mutates `~/.haze/settings.json` |
| Output selection | `--output text\|json\|stream-json` (default `text`) |
| Debug | `--debug` writes JSONL to `~/.haze/logs/`; otherwise no file logging |
| Sessions | Headless runs are one-shot: no durable session created, `--continue` ignored |
| Configuration | Providers/models/API keys ONLY via haze interactive settings; no environment variables read |
| System prompt | None — context comes from `AGENTS.md`/`CLAUDE.md` discovery + the prompt text itself |
| Tool control | None — fixed built-in toolset, no per-tool flags, no confirmation gates |

## Exit codes & errors

- `0` ⇔ terminal status `complete`; any other status exits non-zero. Driven by authoritative agent state, not output parsing. `process.exitCode` semantics guarantee stdout fully drains (no truncated envelope).
- Bad/ambiguous `-m` selector or no configured provider: precise message on **stderr**, non-zero exit, agent never runs.

## `--output stream-json` (primary mode)

- Every stdout line is standalone valid JSON.
- Progress events (see [data-model.md](../data-model.md) for the field tables): `turn_start`, `message_start`, `message_update`, `message_end`, `tool_start`, `tool_end`, `retry`, `context_overflow`, `turn_end` — each with ISO-8601 `at`.
- Exactly one headless `turn_start`/`turn_end` pair per run (retry-nested turns collapsed upstream); `turn_end` carries the authoritative `status`.
- `tool_start`/`tool_end` omit raw tool inputs/outputs (stdout is journal/CI-safe).
- Final line: result envelope, **byte-identical** to `--output json`:

```json
{"type":"result","status":"complete|aborted|failed","result":"<final assistant text>","usage":{"inputTokens":N,"outputTokens":N,"cacheReadTokens":N,"cacheWriteTokens":N,"reasoningTokens":N}}
```

- All five usage fields always present (pinned; `?? 0` normalized upstream).
- `result` is the newline-joined non-hidden assistant text; on `failed` it carries the error message.

## `--output json` (probe mode)

Single-line result envelope only (same schema as above). Used by Kōan solely for the pre-flight quota/auth probe.

## Version boundary

`stream-json` exists from 0.7.0. Older haze rejects the value / behaves as unsupported → surfaces as a normal CLI launch error in Kōan. Minimum supported version is documented in `docs/providers/haze.md`; no runtime version negotiation is performed (YAGNI).
