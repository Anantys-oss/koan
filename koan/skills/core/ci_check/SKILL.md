---
name: ci_check
scope: core
group: code
emoji: 🔧
description: "Check and fix CI failures on a GitHub PR"
version: 1.0.0
audience: hybrid
commands:
  - name: ci_check
    description: "Check and fix CI failures for a PR"
    usage: /ci_check https://github.com/owner/repo/pull/123
handler: handler.py
---
