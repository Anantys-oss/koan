---
name: rebase
scope: core
group: pr
emoji: 🔄
description: "Queue a PR rebase mission (ex: /rebase https://github.com/owner/repo/pull/42). A bare rebase only rebases onto the base branch; add --fix to also apply review feedback."
version: 2.0.0
audience: hybrid
api_exposed: true
caveman: true
model_key: mission
github_enabled: true
github_context_aware: true
commands:
  - name: rebase
    description: "Queue a PR rebase (ex: /rebase https://github.com/owner/repo/pull/42). Bare rebase = rebase only; add --fix to also apply review feedback. Use --now to queue at the top."
    usage: "/rebase [--now] <github-pr-url> [--fix] [additional context]"
    aliases: [rb]
handler: handler.py
---
