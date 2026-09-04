"""Gemini CLI output samples for the Gemini provider tests.

**Provenance — read this before trusting the shapes.** Unlike
``grok_samples.py`` / ``haze_samples.py``, these are *not* a live capture from
a Kōan host. They are constructed from the upstream event schema in
``google-gemini/gemini-cli`` — ``packages/core/src/output/types.ts`` (the
``JsonStreamEvent`` union and ``StreamStats``), the emission sites in
``packages/cli/src/nonInteractiveCli.ts``, and ``docs/cli/headless.md`` —
fetched 2026-09-01. Field names and nesting are taken verbatim from those
sources; concrete token counts and ids are invented placeholders. Re-record
against a real ``gemini`` run when one is available and reconcile any drift
here first, then in ``specs/components/providers.md``.

Content is generic placeholder material only (no private identifiers).

## Stream schema (``--output-format stream-json``) — shape notes

NDJSON events, every one carrying ``type`` + ``timestamp``:

| type | shape | meaning |
|---|---|---|
| ``init`` | ``{"session_id","model"}`` | Session metadata |
| ``message`` | ``{"role","content","delta"?}`` | User echo, then assistant text **deltas** (concatenate with ``""``, not newlines) |
| ``tool_use`` | ``{"tool_name","tool_id","parameters"}`` | Tool call request |
| ``tool_result`` | ``{"tool_id","status","output"?,"error"?}`` | Tool outcome; ``status`` is ``success``/``error`` |
| ``error`` | ``{"severity","message"}`` | Non-fatal warning or system error |
| ``result`` | ``{"status","error"?,"stats"?}`` | Terminal envelope; **no** final text field — text comes from accumulated assistant ``message`` deltas |

``stats`` on ``result`` is snake_case and lives under ``stats``, **not**
``usage``:

```json
{"total_tokens": N, "input_tokens": N, "output_tokens": N,
 "cached": N, "input": N, "duration_ms": N, "tool_calls": N,
 "models": {"<model-id>": {"total_tokens": N, "input_tokens": N,
                           "output_tokens": N, "cached": N, "input": N}}}
```

``cached`` is a SUBSET of ``input_tokens`` (``input`` is the non-cached
remainder), so Kōan accounting subtracts it from input.

## json mode (single object)

``--output-format json`` emits one object: ``{"session_id"?, "response",
"stats", "error"?, "warnings"?}`` — note ``response``, not ``text``.

## Exit codes (docs/cli/headless.md)

``0`` success · ``1`` general/API error · ``42`` input error ·
``53`` turn limit exceeded. Kōan does not currently map ``53`` to its
max-turns handling.
"""

# ---------------------------------------------------------------------------
# stream-json: simple success (ping/pong).
# ---------------------------------------------------------------------------
STREAM_SUCCESS = """\
{"type":"init","timestamp":"2026-09-01T10:00:00.000Z","session_id":"sess-fixture-001","model":"gemini-2.5-pro"}
{"type":"message","timestamp":"2026-09-01T10:00:00.100Z","role":"user","content":"ping"}
{"type":"message","timestamp":"2026-09-01T10:00:01.000Z","role":"assistant","content":"pong","delta":true}
{"type":"result","timestamp":"2026-09-01T10:00:01.500Z","status":"success","stats":{"total_tokens":1420,"input_tokens":1380,"output_tokens":40,"cached":400,"input":980,"duration_ms":1500,"tool_calls":0,"models":{"gemini-2.5-pro":{"total_tokens":1420,"input_tokens":1380,"output_tokens":40,"cached":400,"input":980}}}}
"""

STREAM_SUCCESS_RESULT_TEXT = "pong"

# Usage accounting target after stats extraction + cached subtract:
# input_tokens effective = 1380 - 400 = 980
STREAM_SUCCESS_USAGE = {
    "input_tokens": 980,
    "output_tokens": 40,
    "cache_read_input_tokens": 400,
    "cache_creation_input_tokens": 0,
    "model": "gemini-2.5-pro",
}

# ---------------------------------------------------------------------------
# stream-json: multi-delta assistant text around a tool call.
# ---------------------------------------------------------------------------
STREAM_MULTI_DELTA = """\
{"type":"init","timestamp":"2026-09-01T10:01:00.000Z","session_id":"sess-fixture-002","model":"gemini-2.5-flash"}
{"type":"message","timestamp":"2026-09-01T10:01:00.100Z","role":"user","content":"read the file"}
{"type":"tool_use","timestamp":"2026-09-01T10:01:00.500Z","tool_name":"read_file","tool_id":"tool-fixture-001","parameters":{"file_path":"src/main.py"}}
{"type":"tool_result","timestamp":"2026-09-01T10:01:00.900Z","tool_id":"tool-fixture-001","status":"success","output":"print('hi')"}
{"type":"message","timestamp":"2026-09-01T10:01:01.000Z","role":"assistant","content":"hello","delta":true}
{"type":"message","timestamp":"2026-09-01T10:01:01.100Z","role":"assistant","content":"-from","delta":true}
{"type":"message","timestamp":"2026-09-01T10:01:01.200Z","role":"assistant","content":"-tool","delta":true}
{"type":"result","timestamp":"2026-09-01T10:01:02.000Z","status":"success","stats":{"total_tokens":2600,"input_tokens":2400,"output_tokens":200,"cached":0,"input":2400,"duration_ms":2000,"tool_calls":1,"models":{"gemini-2.5-flash":{"total_tokens":2600,"input_tokens":2400,"output_tokens":200,"cached":0,"input":2400}}}}
"""

STREAM_MULTI_DELTA_RESULT_TEXT = "hello-from-tool"

# ---------------------------------------------------------------------------
# stream-json: failing tool call + non-fatal error event.
# ---------------------------------------------------------------------------
STREAM_TOOL_ERROR = """\
{"type":"init","timestamp":"2026-09-01T10:02:00.000Z","session_id":"sess-fixture-003","model":"gemini-2.5-pro"}
{"type":"tool_use","timestamp":"2026-09-01T10:02:00.400Z","tool_name":"run_shell_command","tool_id":"tool-fixture-002","parameters":{"command":"make test"}}
{"type":"tool_result","timestamp":"2026-09-01T10:02:01.000Z","tool_id":"tool-fixture-002","status":"error","error":{"type":"TOOL_EXECUTION_ERROR","message":"command not found: make"}}
{"type":"error","timestamp":"2026-09-01T10:02:01.100Z","severity":"warning","message":"Tool execution failed, continuing"}
"""

# ---------------------------------------------------------------------------
# stream-json: truncated mid-stream (no result event) — partial fallback.
# ---------------------------------------------------------------------------
STREAM_TRUNCATED = """\
{"type":"init","timestamp":"2026-09-01T10:03:00.000Z","session_id":"sess-fixture-004","model":"gemini-2.5-pro"}
{"type":"message","timestamp":"2026-09-01T10:03:00.500Z","role":"assistant","content":"Partial","delta":true}
{"type":"message","timestamp":"2026-09-01T10:03:00.600Z","role":"assistant","content":" progress","delta":true}
{"type":"message","timestamp":"2026-09-01T10:03:00.700Z","role":"assistant","content":" so far","delta":true}
"""

STREAM_TRUNCATED_PARTIAL_TEXT = "Partial progress so far"

# ---------------------------------------------------------------------------
# --output-format json single object (probe / non-stream mode). ``stats`` here
# is the raw SessionMetrics shape, NOT the flattened StreamStats above.
# ---------------------------------------------------------------------------
JSON_OBJECT_SUCCESS = """\
{
  "session_id": "sess-fixture-005",
  "response": "ok",
  "stats": {
    "models": {
      "gemini-2.5-pro": {
        "api": {"totalRequests": 1, "totalErrors": 0, "totalLatencyMs": 620},
        "tokens": {"prompt": 900, "candidates": 12, "total": 912, "cached": 0,
                   "thoughts": 0, "tool": 0}
      }
    },
    "tools": {"totalCalls": 0},
    "files": {"totalLinesAdded": 0, "totalLinesRemoved": 0}
  }
}
"""

JSON_OBJECT_SUCCESS_TEXT = "ok"

# ---------------------------------------------------------------------------
# Failure text samples (stderr) for quota / auth detection.
# ---------------------------------------------------------------------------
QUOTA_STDERR = (
    "[API Error: {\"error\":{\"code\":429,"
    "\"message\":\"Resource has been exhausted (e.g. check quota).\","
    "\"status\":\"RESOURCE_EXHAUSTED\"}}]"
)

AUTH_STDERR = (
    "[API Error: {\"error\":{\"code\":401,"
    "\"message\":\"API key not valid. Please pass a valid API key.\","
    "\"status\":\"UNAUTHENTICATED\"}}]"
)
