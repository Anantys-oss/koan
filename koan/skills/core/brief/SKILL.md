---
name: brief
scope: core
group: status
emoji: 📋
description: Daily digest — pending missions, recent completions, quota health, journal highlights
version: 1.0.0
audience: bridge
api_exposed: true
worker: true
commands:
  - name: brief
    description: Show daily digest (or schedule daily delivery)
    usage: /brief [schedule HH:MM | off]
    aliases: [digest]
handler: handler.py
---
