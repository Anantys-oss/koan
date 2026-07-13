---
type: doc
title: "Bridge memory profile and retention controls"
description: "How awake.py bounds RSS over long uptime: tail-read history, periodic mid-session compaction, one-cycle mission-store read cache, and an opt-in MemoryMonitor watchdog backstop."
tags: [architecture, bridge, memory]
created: 2026-07-13
updated: 2026-07-13
---

# Bridge memory profile and retention controls

`awake.py` is a long-lived process (days of uptime). Before #2354 its RSS
ratcheted from a ~40 MB baseline toward hundreds of MB and only a restart
reset it — the signature of arena fragmentation from repeated large,
short-lived allocations, not a classic reference leak.

## Root causes (July 8 deploy)

- `load_recent_history` re-read and re-parsed the entire append-only
  `conversation-history.jsonl` on every chat message.
- `compact_history` ran only at startup, so the file grew unbounded across a
  session (four writers: user messages, assistant replies, error branches,
  and every successful outbox notification).
- `_build_chat_prompt` opened/closed several SQLite connections per chat
  message after the store-existence gate was removed (`4c0c60c4`).

## Mitigations

1. **Tail-read** — `load_recent_history` uses `locked_jsonl_tail`
   (seek-from-EOF, chunked, `LOCK_SH`).
2. **Periodic compaction** — `_bridge_loop` re-runs `compact_history` on a
   timer (`conversation.compact_interval_seconds`, default 3600, floored at
   300; 0 disables).
3. **Read-cache** — `_read_sections_cached` caches the mission-store read for
   one poll cycle (3 s).
4. **Watchdog backstop** — an opt-in `MemoryMonitor`
   (`memory_monitor.bridge.enabled`, default threshold 600 MB) samples RSS
   each cycle and self-restarts via `reexec_bridge()` only when all worker
   lanes are idle.

## Configuration

```yaml
# instance/config.yaml
memory_monitor:
  bridge:
    enabled: true
    threshold_mb: 600
    sustained_samples: 3
conversation:
  compact_interval_seconds: 3600
```

## Verification

After a long idle period, `ps ax -o pid,rss,etime,command --sort=-rss`
should show `awake.py` near its boot RSS (~40 MB). Remaining tall plateaus on
the deployment memory graph should correlate 1:1 with live `claude -p`
subprocesses.

See also: [shared-state](shared-state.md), `specs/components/bridge.md`.
