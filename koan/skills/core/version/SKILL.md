---
name: version
scope: core
group: status
emoji: 🏷️
description: Show Kōan version (tag, commit hash, commits ahead)
version: 1.0.0
audience: bridge
chat_confirmable: true
commands:
  - name: version
    description: Show current Kōan version
    aliases: [ver, v]
handler: handler.py
---
