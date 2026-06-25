---
name: refactor
scope: core
group: pr
emoji: 🛠️
description: "Queue a PR refactor mission (ex: /refactor https://github.com/owner/repo/pull/42)"
version: 2.0.0
audience: hybrid
caveman: true
model_key: mission
github_enabled: true
github_context_aware: true
commands:
  - name: refactor
    description: "Refactor a PR's code for clarity, then push & comment (ex: /refactor <pr-url> [focus]). Use --now to queue at the top."
    aliases: [rf]
handler: handler.py
---
