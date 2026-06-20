# Prompt Guard

Input-side defense against prompt injection in missions and external data.

## What it does

`prompt_guard.py` scans incoming mission text (from Telegram and GitHub
@mentions) before it reaches the agent. It detects:

- **Instruction overrides** — "ignore previous instructions", "new system prompt"
- **Role confusion** — "you are now", "act as"
- **Secret extraction** — requests for API keys, tokens, env vars
- **Shell injection** — attempts to inject shell commands
- **Jailbreak markers** — DAN-style prompts, base64-encoded payloads

It also provides `fence_external_data()` to wrap untrusted content (PR
bodies, review comments, issue text) with cryptographic markers so the
model treats it as data, not instructions.

## Configuration

```yaml
prompt_guard:
  enabled: true       # Master switch (default: true)
  block_mode: true    # true = reject mission (default), false = warn + quarantine
```

### block_mode

- **`true` (default)**: Suspicious missions are rejected outright. The
  mission is not queued. A warning is sent to Telegram.
- **`false`**: Suspicious missions are quarantined with a warning but
  still queued. Use this to monitor false positives before enforcing.

The default was changed to `true` to follow secure-by-default principles.
Operators who want to audit detections before enforcing can set
`block_mode: false` explicitly.

## Complementary defenses

- **`outbox_scanner.py`** — output-side defense. Scans agent output
  before it reaches Telegram for secret leaks and suspicious content.
- **Data fencing** (`fence_external_data`) — wraps untrusted content
  with BEGIN/END markers containing a random nonce, making it harder
  for injected instructions to escape the data boundary.
- **OPSEC rules** in the system prompt — instruct the agent to treat
  mission text, PR bodies, and code as data, not instructions.
- **Assembly-time memory scanning** (`memory_manager.sanitize_memory_entry`)
  — last line of defense before stored memory reaches the LLM. Every entry
  returned by `read_memory_window()` is run through `scan_mission_text()`;
  flagged entries have their content replaced with
  `[BLOCKED: injection pattern detected]` while the original line stays in
  the JSONL truth log for audit. This catches entries written before the
  intake guard existed, since scanning runs at read time, not write time.
