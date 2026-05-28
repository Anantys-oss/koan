---
name: config_check
scope: core
group: config
emoji: 🔧
description: Detect config.yaml drift against the instance.example template
version: 1.0.0
audience: bridge
commands:
  - name: config_check
    description: Compare instance/config.yaml against instance.example/config.yaml
    usage: /config_check
    aliases: [cfgcheck, configcheck]
handler: handler.py
---
