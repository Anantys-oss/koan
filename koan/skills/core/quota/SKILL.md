---
name: quota
scope: core
group: status
description: Check LLM quota live (no cache)
version: 1.0.0
audience: bridge
commands:
  - name: quota
    description: Live quota and token usage metrics
    usage: /quota
    aliases: [q]
handler: handler.py
---
