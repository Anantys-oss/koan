---
name: claudemd
scope: core
group: code
emoji: 📝
description: Refresh or create CLAUDE.md for a project, or sync Kōan's learnings into it
version: 1.1.0
audience: hybrid
caveman: false
commands:
  - name: claudemd
    description: Refresh CLAUDE.md, or sync Kōan's learnings into it
    usage: /claudemd <project-name> [learnings]
    aliases: [claude, claude.md, claude_md]
handler: handler.py
---
