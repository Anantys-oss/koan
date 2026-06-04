---
name: explain
scope: core
group: code
emoji: 💡
description: "Explain a PR's intent and changes in plain language (ex: /explain https://github.com/owner/repo/pull/42)"
version: 1.0.0
audience: hybrid
caveman: false
github_enabled: true
github_context_aware: true
commands:
  - name: explain
    description: "Explain a PR's changes in simple words with examples and alternative approaches"
    usage: "/explain [--now] <github-pr-url>"
    aliases: [xp]
handler: handler.py
---
