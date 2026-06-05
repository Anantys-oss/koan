---
name: alias
scope: core
group: config
emoji: 🔗
description: Create short aliases for project names (e.g. /alias Template2 tt then /tt queues missions for Template2)
version: 1.0.0
audience: bridge
commands:
  - name: alias
    description: Create or list project aliases
    usage: "/alias <project> <shortcut> — create alias. /alias --rm <shortcut> — remove alias. /alias — list all aliases."
    aliases: []
  - name: unalias
    description: Remove a project alias
    usage: /unalias <shortcut>
    aliases: []
handler: handler.py
---
