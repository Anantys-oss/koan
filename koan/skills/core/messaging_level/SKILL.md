---
name: messaging_level
scope: core
group: config
emoji: 🔉
description: Show or set bridge verbosity (debug / normal)
version: 1.0.0
audience: bridge
commands:
  - name: messaging_level
    description: Show or set bridge verbosity level
    aliases: [msglevel]
handler: handler.py
---

Show or set the bridge verbosity level.

- `normal` (default): quiet, operator-focused. Failures, command replies, and
  one-line PR results still come through.
- `debug`: full lifecycle narration (mission start/end, per-mention queue lines).

Every suppressed message is still written to the logs.
