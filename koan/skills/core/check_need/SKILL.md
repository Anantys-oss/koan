---
name: check_need
scope: core
group: code
emoji: 🔎
description: "Check if a PR or issue is still needed given the current state of the repo"
version: 1.0.0
audience: hybrid
github_enabled: true
github_context_aware: true
worker: true
commands:
  - name: check_need
    description: "Analyze whether a PR's changes or an issue's request is still relevant"
    usage: "/check_need <github-pr-or-issue-url>"
    aliases: [need, needs]
handler: handler.py
---
