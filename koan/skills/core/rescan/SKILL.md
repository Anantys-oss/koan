---
name: rescan
scope: core
group: config
emoji: 🔍
description: Re-check all project workspaces for remote HEAD changes (e.g. master → main)
version: 1.0.0
audience: bridge
commands:
  - name: rescan
    description: Scan all projects for remote default branch changes and update workspaces
    usage: /rescan
    aliases: [rescan_heads]
handler: handler.py
---
