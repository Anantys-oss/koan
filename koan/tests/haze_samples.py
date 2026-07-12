"""Recorded haze CLI output samples shared across the haze provider tests.

These are verbatim-shaped captures of haze >= 0.7.0 headless output
(``haze --output stream-json`` / ``--output json``), per the external
contract in specs/006-haze-cli-provider/contracts/haze-cli-interface.md:
field names and casing (camelCase usage), event vocabulary, ISO-8601 ``at``
timestamps, and the terminal result envelope that is byte-identical between
``json`` and ``stream-json`` modes. They are the "recorded sample" the
providers component spec's change protocol requires usage extraction to be
verified against. Content is generic placeholder material only.
"""

# ---------------------------------------------------------------------------
# Complete stream-json transcript: one successful headless turn with tool use.
# Final line is the result envelope (all five usage fields always present).
# ---------------------------------------------------------------------------
STREAM_SUCCESS = """\
{"type":"turn_start","request":"Summarize the repo layout","at":"2026-07-10T17:30:00.000Z"}
{"type":"message_start","id":"msg_1","role":"assistant","at":"2026-07-10T17:30:01.100Z"}
{"type":"message_update","id":"msg_1","text":"Looking at the","at":"2026-07-10T17:30:01.400Z"}
{"type":"message_update","id":"msg_1","text":"Looking at the project layout now.","at":"2026-07-10T17:30:01.700Z"}
{"type":"message_end","id":"msg_1","text":"Looking at the project layout now.","at":"2026-07-10T17:30:02.000Z"}
{"type":"tool_start","id":"tool_1","name":"listFiles","at":"2026-07-10T17:30:02.200Z"}
{"type":"tool_end","id":"tool_1","name":"listFiles","success":true,"durationMs":412,"at":"2026-07-10T17:30:02.700Z"}
{"type":"tool_start","id":"tool_2","name":"readFile","at":"2026-07-10T17:30:03.000Z"}
{"type":"tool_end","id":"tool_2","name":"readFile","success":true,"durationMs":88,"at":"2026-07-10T17:30:03.100Z"}
{"type":"message_start","id":"msg_2","role":"assistant","at":"2026-07-10T17:30:04.000Z"}
{"type":"message_end","id":"msg_2","text":"The repo has two top-level packages: app and tests.","at":"2026-07-10T17:30:05.000Z"}
{"type":"turn_end","request":"Summarize the repo layout","status":"complete","at":"2026-07-10T17:30:05.200Z"}
{"type":"result","status":"complete","result":"Looking at the project layout now.\\nThe repo has two top-level packages: app and tests.","usage":{"inputTokens":5230,"outputTokens":410,"cacheReadTokens":1200,"cacheWriteTokens":300,"reasoningTokens":57}}
"""

# Expected extraction targets for STREAM_SUCCESS (keep next to the fixture so
# tests do not re-derive the mapping independently of the recorded data).
STREAM_SUCCESS_RESULT_TEXT = (
    "Looking at the project layout now.\n"
    "The repo has two top-level packages: app and tests."
)
STREAM_SUCCESS_USAGE = {
    "inputTokens": 5230,
    "outputTokens": 410,
    "cacheReadTokens": 1200,
    "cacheWriteTokens": 300,
    "reasoningTokens": 57,
}

# ---------------------------------------------------------------------------
# Transcript with a hidden message_end segment (hidden text is excluded from
# both haze's own result joining and Koan's partial-output fallback).
# ---------------------------------------------------------------------------
STREAM_HIDDEN_SEGMENT = """\
{"type":"turn_start","request":"do the thing","at":"2026-07-10T17:31:00.000Z"}
{"type":"message_start","id":"msg_1","role":"assistant","at":"2026-07-10T17:31:01.000Z"}
{"type":"message_end","id":"msg_1","text":"internal scratch note","hidden":true,"at":"2026-07-10T17:31:02.000Z"}
{"type":"message_start","id":"msg_2","role":"assistant","at":"2026-07-10T17:31:03.000Z"}
{"type":"message_end","id":"msg_2","text":"Visible answer.","at":"2026-07-10T17:31:04.000Z"}
{"type":"turn_end","request":"do the thing","status":"complete","at":"2026-07-10T17:31:05.000Z"}
{"type":"result","status":"complete","result":"Visible answer.","usage":{"inputTokens":900,"outputTokens":40,"cacheReadTokens":0,"cacheWriteTokens":0,"reasoningTokens":0}}
"""

# ---------------------------------------------------------------------------
# Mid-stream death: the CLI was killed before turn_end / the result envelope.
# The partial-output fallback must recover the non-hidden message_end text.
# ---------------------------------------------------------------------------
STREAM_TRUNCATED = """\
{"type":"turn_start","request":"long mission","at":"2026-07-10T17:32:00.000Z"}
{"type":"message_start","id":"msg_1","role":"assistant","at":"2026-07-10T17:32:01.000Z"}
{"type":"message_update","id":"msg_1","text":"Partial progress on","at":"2026-07-10T17:32:01.500Z"}
{"type":"message_end","id":"msg_1","text":"Partial progress on the first step.","at":"2026-07-10T17:32:02.000Z"}
{"type":"tool_start","id":"tool_1","name":"bash","at":"2026-07-10T17:32:03.000Z"}
"""
STREAM_TRUNCATED_PARTIAL_TEXT = "Partial progress on the first step."

# ---------------------------------------------------------------------------
# Failed turn: retries exhausted; turn_end and envelope carry status "failed",
# exit code is non-zero, result carries the error message.
# ---------------------------------------------------------------------------
STREAM_FAILED = """\
{"type":"turn_start","request":"do the thing","at":"2026-07-10T17:33:00.000Z"}
{"type":"retry","attempt":1,"maxAttempts":3,"delayMs":1000,"error":"Upstream provider returned 500 Internal Server Error","at":"2026-07-10T17:33:02.000Z"}
{"type":"retry","attempt":2,"maxAttempts":3,"delayMs":2000,"error":"Upstream provider returned 500 Internal Server Error","at":"2026-07-10T17:33:05.000Z"}
{"type":"turn_end","request":"do the thing","status":"failed","at":"2026-07-10T17:33:09.000Z"}
{"type":"result","status":"failed","result":"Model call failed after 3 attempts: Upstream provider returned 500 Internal Server Error","usage":{"inputTokens":1500,"outputTokens":0,"cacheReadTokens":0,"cacheWriteTokens":0,"reasoningTokens":0}}
"""

# Aborted turn (interrupt): status "aborted", non-zero exit.
STREAM_ABORTED = """\
{"type":"turn_start","request":"do the thing","at":"2026-07-10T17:34:00.000Z"}
{"type":"message_start","id":"msg_1","role":"assistant","at":"2026-07-10T17:34:01.000Z"}
{"type":"turn_end","request":"do the thing","status":"aborted","at":"2026-07-10T17:34:02.000Z"}
{"type":"result","status":"aborted","result":"","usage":{"inputTokens":800,"outputTokens":12,"cacheReadTokens":0,"cacheWriteTokens":0,"reasoningTokens":0}}
"""

# Context-overflow events (recovered and not).
STREAM_CONTEXT_OVERFLOW_RECOVERED = (
    '{"type":"context_overflow","recovered":true,'
    '"error":"context window exceeded; compacted and retried",'
    '"at":"2026-07-10T17:35:00.000Z"}'
)
STREAM_CONTEXT_OVERFLOW_FATAL = (
    '{"type":"context_overflow","recovered":false,'
    '"error":"context window exceeded",'
    '"at":"2026-07-10T17:35:10.000Z"}'
)

# ---------------------------------------------------------------------------
# --output json single-envelope samples (probe mode). Byte-identical schema to
# the stream-json terminal line.
# ---------------------------------------------------------------------------
JSON_ENVELOPE_SUCCESS = (
    '{"type":"result","status":"complete","result":"ok",'
    '"usage":{"inputTokens":420,"outputTokens":3,"cacheReadTokens":0,'
    '"cacheWriteTokens":0,"reasoningTokens":0}}'
)
JSON_ENVELOPE_ZERO_USAGE = (
    '{"type":"result","status":"complete","result":"ok",'
    '"usage":{"inputTokens":0,"outputTokens":0,"cacheReadTokens":0,'
    '"cacheWriteTokens":0,"reasoningTokens":0}}'
)

# ---------------------------------------------------------------------------
# Failure-classification samples. Haze is multi-backend, so these are the
# generic error texts its OpenAI/OpenRouter/local backends produce.
# ---------------------------------------------------------------------------
STDERR_QUOTA_429 = "Model call failed: HTTP 429 Too Many Requests: rate limit exceeded, retry-after: 60"
STDERR_QUOTA_INSUFFICIENT = "Model call failed: insufficient_quota: You exceeded your current quota, please check your plan and billing details."
STDOUT_QUOTA_ENVELOPE = (
    '{"type":"result","status":"failed",'
    '"result":"Model call failed after 3 attempts: 429 status: rate limit exceeded",'
    '"usage":{"inputTokens":0,"outputTokens":0,"cacheReadTokens":0,'
    '"cacheWriteTokens":0,"reasoningTokens":0}}'
)

STDERR_AUTH_401 = "Model call failed: unexpected status 401 Unauthorized: invalid api key provided"
STDERR_AUTH_KEY = "Authentication failed: API key missing or expired for provider openai"
STDERR_NO_PROVIDER = "No model provider configured. Run /provider to choose or add a provider."
STDERR_BAD_MODEL = "No configured model named openai:gpt-nonexistent. Run /provider, select a provider, then add models."

# A successful run whose ASSISTANT PROSE merely talks about rate limits —
# must never be classified as quota exhaustion (exit code 0).
STDOUT_BENIGN_PROSE_ENVELOPE = (
    '{"type":"result","status":"complete",'
    '"result":"I added retry handling so the client backs off when the API '
    'returns a rate limit error such as HTTP 429.",'
    '"usage":{"inputTokens":2100,"outputTokens":180,"cacheReadTokens":0,'
    '"cacheWriteTokens":0,"reasoningTokens":0}}'
)
