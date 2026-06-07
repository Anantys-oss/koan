---
name: sdlc
scope: core
group: code
emoji: 🔄
description: "Run a multi-phase SDLC workflow: Research → Architecture → Planning → Implementation → Review → Docs"
version: 1.0.0
audience: hybrid
caveman: true
worker: true
github_enabled: true
github_context_aware: true
commands:
  - name: sdlc
    description: "Start or resume a full SDLC workflow for a GitHub issue"
    usage: "/sdlc <issue-name> [description] [--resume] [--plan] [--implement] [--review]"
    aliases: [sdlc_run]
handler: handler.py
---
