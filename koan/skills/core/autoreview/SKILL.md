---
name: autoreview
scope: core
group: config
emoji: "🔍"
description: Toggle automatic review+rebase after PR creation per project
version: 1.0.0
audience: bridge
commands:
  - name: autoreview
    description: Enable automatic review+rebase or show status
    usage: /autoreview [project|all|none]
    aliases: [auto_review]
  - name: noautoreview
    description: Disable automatic review+rebase for a project
    usage: /noautoreview [project]
    aliases: []
handler: handler.py
---
