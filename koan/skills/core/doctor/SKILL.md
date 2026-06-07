---
name: doctor
scope: core
group: status
emoji: 🩺
description: Run diagnostic self-checks on Kōan configuration and health, with optional auto-repair
version: 1.1.0
audience: bridge
chat_confirmable: true
worker: true
commands:
  - name: doctor
    description: Run diagnostic self-checks
    usage: /doctor [--full] [--fix]
    aliases: [diag]
handler: handler.py
---
