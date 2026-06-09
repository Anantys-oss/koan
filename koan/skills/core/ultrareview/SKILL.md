---
name: ultrareview
scope: core
group: code
emoji: 🔬
description: "Queue an ultra-thorough code review for a PR — architecture + silent-failure passes combined (ex: /ultrareview https://github.com/owner/repo/pull/42)"
version: 1.0.0
audience: hybrid
caveman: false
github_enabled: true
github_context_aware: true
commands:
  - name: ultrareview
    description: "Queue the most thorough review Kōan can run: architecture-focused main pass + silent-failure-hunter pass in a single comment. Use --now to queue at the top."
    usage: "/ultrareview [--now] <github-pr-url> [context]"
    aliases: [urv, ultra_review]
handler: handler.py
---
