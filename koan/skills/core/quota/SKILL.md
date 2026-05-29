---
name: quota
scope: core
group: status
emoji: 📊
description: Check LLM quota, override used %, or reset estimates
version: 1.2.0
audience: bridge
commands:
  - name: quota
    description: Live quota metrics, override used %, or reset estimates
    usage: /quota [used_%|reset]
    aliases: [q]
handler: handler.py
---
