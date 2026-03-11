---
name: fix
scope: core
group: code
description: "Fix a GitHub issue end-to-end (ex: /fix https://github.com/owner/repo/issues/42)"
version: 1.0.0
audience: hybrid
github_enabled: true
github_context_aware: true
commands:
  - name: fix
    description: "Queue a fix mission for a GitHub issue — understand, plan, test, implement, and submit a PR"
    usage: "/fix <issue-url> [additional context]"
handler: handler.py
---
