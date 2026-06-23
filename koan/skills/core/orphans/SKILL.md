---
name: orphans
scope: core
group: pr
emoji: 🌿
description: Recover orphan branches by rebasing onto main and creating draft PRs
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: orphans
    description: Recover orphan branches — rebase + draft PR for each
    usage: /orphans <project_name>
    aliases: [orphan]
handler: handler.py
---
