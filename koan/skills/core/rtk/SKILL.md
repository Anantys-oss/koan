---
name: rtk
scope: core
group: system
emoji: 🪓
description: Manage optional rtk integration (https://github.com/rtk-ai/rtk) for compressed tool output
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: rtk
    description: Show rtk detection status (binary, version, hook, jq, project setting)
    usage: /rtk [setup|uninstall|gain|on|off]
    aliases: []
handler: handler.py
---
