---
name: done
scope: core
group: status
description: List merged PRs from the last 24 hours across all projects
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: done
    description: Show PRs merged in the last 24 hours
    usage: /done [project] [--hours=N]
    aliases: [merged]
handler: handler.py
---
