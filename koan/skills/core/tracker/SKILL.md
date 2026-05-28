---
name: tracker
scope: core
group: config
emoji: 🧭
description: Show or configure per-project issue tracker settings
version: 1.0.0
audience: bridge
commands:
  - name: tracker
    description: Show or set issue tracker routing for projects
    usage: /tracker, /tracker set <project> github|jira ...
handler: handler.py
---

