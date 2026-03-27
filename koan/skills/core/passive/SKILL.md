---
name: passive
scope: core
group: config
description: Passive mode — read-only, no missions or exploration. Use /active to resume.
version: 1.0.0
audience: bridge
commands:
  - name: passive
    description: Activate passive mode (read-only, no execution)
    usage: /passive [duration] — no duration = indefinite. Examples: /passive, /passive 4h, /passive 2h30m
    aliases: []
  - name: active
    description: Deactivate passive mode, resume normal execution
    aliases: []
handler: handler.py
---
