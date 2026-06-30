---
name: inbox
scope: core
group: status
emoji: 📬
description: Force an immediate check of GitHub notifications and show pending mail count
version: 1.0.0
audience: bridge
chat_confirmable: true
commands:
  - name: inbox
    description: Check GitHub inbox for new notifications to process
    aliases: []
    usage: /inbox
handler: handler.py
---
