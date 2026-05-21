---
name: cancel
scope: core
group: missions
emoji: ❌
description: Cancel one or more pending missions
version: 1.1.0
audience: bridge
commands:
  - name: cancel
    description: Cancel one or more pending missions
    usage: /cancel <n>, /cancel 3,5,7, /cancel <keyword>
    aliases: [remove, clear, rm]
handler: handler.py
---
