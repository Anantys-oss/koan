"""Recorded Grok Build CLI output samples for the Grok provider tests.

Captured against ``grok 0.2.101`` (stable) headless mode on 2026-07-15:

- ``grok -p … --output-format streaming-json --always-approve --max-turns N``
- ``grok -p … --output-format json --always-approve --max-turns N``

These are the "recorded samples" the providers component-spec change protocol
expects usage/text extraction to be verified against. Content is generic
placeholder material only (no private identifiers).

## Stream schema (streaming-json) — shape notes

NDJSON events observed:

| type | shape | meaning |
|---|---|---|
| ``thought`` | ``{"type":"thought","data":"<delta>"}`` | Reasoning token deltas (display only) |
| ``text`` | ``{"type":"text","data":"<delta>"}`` | Assistant text **deltas** (concatenate with ``""``, not newlines) |
| ``end`` | ``{"type":"end","stopReason", "sessionId", "requestId", "usage", "num_turns", "modelUsage"}`` | Terminal envelope; **no** final text field — text comes from accumulated ``text`` deltas |

``usage`` on ``end`` is **snake_case** API-style:

```json
{"input_tokens": N, "output_tokens": N, "cache_read_input_tokens": N,
 "reasoning_tokens": N, "total_tokens": N}
```

``modelUsage`` is a per-model map with **camelCase** counters (Claude-like):

```json
{"grok-4.5": {"inputTokens": N, "outputTokens": N,
              "cacheReadInputTokens": N, "modelCalls": N}}
```

No separate ``tool_start`` / ``tool_end`` events were observed on a short
shell-tool turn in 0.2.101 — tools appear to run without NDJSON tool events
(only thought/text/end). Treat tool progress as best-effort.

## json mode (single object)

``--output-format json`` emits **one** JSON object (pretty-printed), not a
``type: result`` envelope:

```json
{"text": "…", "stopReason": "EndTurn", "sessionId": "…", "requestId": "…",
 "thought": "…", "usage": {…snake_case…}, "num_turns": 1, "modelUsage": {…}}
```

## Capability notes (Grok Build 0.2.101 help)

Supported flags useful to Koan:

- ``-p`` / ``--single`` headless prompt
- ``--output-format`` ``plain`` | ``json`` | ``streaming-json``
- ``-m`` / ``--model``
- ``--max-turns``
- ``--always-approve``
- ``--permission-mode`` (default|acceptEdits|auto|dontAsk|bypassPermissions|plan)
- ``--tools`` / ``--disallowed-tools`` (comma-separated built-ins)
- ``--allow`` / ``--deny`` (permission rules)
- ``--rules`` (append to system prompt)
- ``--system-prompt-override``
- ``--reasoning-effort`` / ``--effort``
- ``-r`` / ``--resume``, ``-c`` / ``--continue``, ``-s`` / ``--session-id``
- ``--cwd``, ``--prompt-file``

Not observed / not used in MVP:

- ``--no-auto-update`` (mentioned in online docs; not present in 0.2.101 ``--help``)
- Claude-style ``--output-format stream-json`` spelling (Grok uses ``streaming-json``)
"""

# ---------------------------------------------------------------------------
# streaming-json: simple success (ping/pong). Real capture, redacted session IDs
# replaced with stable placeholders for fixtures.
# ---------------------------------------------------------------------------
STREAM_SUCCESS = """\
{"type":"thought","data":"The"}
{"type":"thought","data":" user"}
{"type":"thought","data":" wants"}
{"type":"thought","data":" a"}
{"type":"thought","data":" simple"}
{"type":"thought","data":" reply"}
{"type":"thought","data":"."}
{"type":"text","data":"pong"}
{"type":"end","stopReason":"EndTurn","sessionId":"sess-fixture-001","requestId":"req-fixture-001","usage":{"input_tokens":21415,"cache_read_input_tokens":6016,"output_tokens":28,"reasoning_tokens":23,"total_tokens":27459},"num_turns":1,"modelUsage":{"grok-4.5":{"inputTokens":21415,"outputTokens":28,"cacheReadInputTokens":6016,"modelCalls":1}}}
"""

STREAM_SUCCESS_RESULT_TEXT = "pong"

# Usage accounting target after snake_case extraction + cache-read subtract:
# input_tokens effective = 21415 - 6016 = 15399
STREAM_SUCCESS_USAGE = {
    "input_tokens": 15399,
    "output_tokens": 28,
    "cache_read_input_tokens": 6016,
    "cache_creation_input_tokens": 0,
}

# ---------------------------------------------------------------------------
# streaming-json: multi-delta assistant text (tool-assisted reply).
# ---------------------------------------------------------------------------
STREAM_MULTI_DELTA = """\
{"type":"thought","data":"Run"}
{"type":"thought","data":" the"}
{"type":"thought","data":" command"}
{"type":"thought","data":"."}
{"type":"text","data":"hello"}
{"type":"text","data":"-"}
{"type":"text","data":"from"}
{"type":"text","data":"-"}
{"type":"text","data":"tool"}
{"type":"end","stopReason":"EndTurn","sessionId":"sess-fixture-002","requestId":"req-fixture-002","usage":{"input_tokens":21550,"cache_read_input_tokens":33408,"output_tokens":71,"reasoning_tokens":59,"total_tokens":55029},"num_turns":2,"modelUsage":{"grok-4.5":{"inputTokens":21550,"outputTokens":71,"cacheReadInputTokens":33408,"modelCalls":2}}}
"""

STREAM_MULTI_DELTA_RESULT_TEXT = "hello-from-tool"

# ---------------------------------------------------------------------------
# streaming-json: truncated mid-stream (no end event) — partial fallback.
# ---------------------------------------------------------------------------
STREAM_TRUNCATED = """\
{"type":"thought","data":"Working"}
{"type":"text","data":"Partial"}
{"type":"text","data":" progress"}
{"type":"text","data":" so"}
{"type":"text","data":" far"}
"""

STREAM_TRUNCATED_PARTIAL_TEXT = "Partial progress so far"

# ---------------------------------------------------------------------------
# --output-format json single object (probe / non-stream mode).
# ---------------------------------------------------------------------------
JSON_OBJECT_SUCCESS = """\
{
  "text": "ok",
  "stopReason": "EndTurn",
  "sessionId": "sess-fixture-003",
  "requestId": "req-fixture-003",
  "thought": "Short acknowledgement.",
  "usage": {
    "input_tokens": 21414,
    "cache_read_input_tokens": 6016,
    "output_tokens": 19,
    "reasoning_tokens": 14,
    "total_tokens": 27449
  },
  "num_turns": 1,
  "modelUsage": {
    "grok-4.5": {
      "inputTokens": 21414,
      "outputTokens": 19,
      "cacheReadInputTokens": 6016,
      "modelCalls": 1
    }
  }
}
"""

JSON_OBJECT_SUCCESS_TEXT = "ok"
