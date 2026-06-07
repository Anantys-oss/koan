---
name: check_notifications
scope: core
group: status
emoji: 🔔
description: Force an immediate check of GitHub and Jira notifications
version: 1.0.0
audience: bridge
chat_confirmable: true
commands:
  - name: check_notifications
    description: Trigger immediate notification check (bypasses backoff)
    aliases: [read]
    usage: /check_notifications
handler: handler.py
---
