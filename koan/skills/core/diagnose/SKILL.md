---
name: diagnose
scope: core
group: code
emoji: 🔍
description: "Analyze the last mission failure and queue a fix attempt"
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: diagnose
    description: "Find the last failed mission, extract context from journals, and queue a fix mission"
    usage: "/diagnose [project]"
    aliases: [dx]
handler: handler.py
---
