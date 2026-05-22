---
name: doc
scope: core
group: code
emoji: "\U0001F4DA"
description: Extract and generate structured documentation from a project codebase
version: 1.0.0
audience: hybrid
github_enabled: true
github_context_aware: true
commands:
  - name: doc
    description: Investigate a project codebase and produce structured documentation under docs/
    usage: /doc <project-name> [categories] [--mode=create|update|replace]
    aliases: [docs]
handler: handler.py
worker: true
---
