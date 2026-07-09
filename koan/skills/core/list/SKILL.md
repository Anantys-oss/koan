---
name: list
scope: core
group: missions
emoji: 📋
description: List missions by state
version: 1.1.0
audience: bridge
chat_confirmable: true
commands:
  - name: list
    description: List missions — default pending + in progress; also done/failed/all
    usage: /list [pending|in_progress|done|failed|all]
    aliases: [queue, ls]
handler: handler.py
---
