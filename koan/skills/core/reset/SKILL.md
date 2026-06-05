---
name: reset
scope: core
group: system
emoji: 🔄
description: Reset the run counter to zero without restarting
version: 1.0.0
audience: bridge
commands:
  - name: reset
    description: Reset run counter to 0
    usage: "/reset -- reset mission counter to 0 (continues running or resumes from max_runs pause)"
handler: handler.py
---
