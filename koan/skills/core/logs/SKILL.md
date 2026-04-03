---
name: logs
scope: core
group: status
emoji: 📜
description: Show last lines from run and/or awake logs
version: 1.1.0
audience: bridge
commands:
  - name: logs
    description: Show last 20 lines from logs (run|awake|all, default run)
    usage: /logs [run|awake|all]
handler: handler.py
---
