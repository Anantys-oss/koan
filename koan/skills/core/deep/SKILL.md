---
name: deep
scope: core
group: ideas
emoji: 🧠
description: Launch an on-demand deep exploration session for a project
version: 1.0.0
audience: hybrid
worker: true
commands:
  - name: deep
    description: Start a deep autonomous exploration of a project
    aliases: []
    usage: |
      /deep [project] [focus context]

      Launches a thorough, autonomous exploration session on a project.
      Unlike /ai (which suggests quick wins), /deep dives deep into the
      codebase with full tool access — reading code, running tests,
      checking CI, and generating detailed follow-up missions.

      Runs with higher turn limits for thorough analysis.

      Examples:
        /deep                            — deep explore a random project
        /deep koan                       — deep explore the koan project
        /deep koan error handling paths  — focused deep dive
handler: handler.py
---
