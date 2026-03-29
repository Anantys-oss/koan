---
name: done
scope: core
group: status
emoji: ✔️
description: List merged and open PRs from the last 24 hours across all projects
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: done
    description: Show merged and open PRs from the last 24 hours
    usage: /done [project] [--hours=N]
    aliases: [merged]
handler: handler.py
---
