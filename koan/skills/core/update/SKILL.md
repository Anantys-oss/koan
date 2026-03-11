---
name: update
scope: core
group: system
description: Update Kōan to latest upstream code and restart
version: 1.0.0
audience: bridge
commands:
  - name: update
    description: Pull latest code from upstream and restart both processes
    aliases: [upgrade, restart]
    usage: "/update -- pull latest code and restart (alias: /restart, /upgrade)"
handler: handler.py
---
