---
name: ai
scope: core
group: ideas
emoji: ✨
description: Queue an AI exploration mission for a project
version: 1.1.0
audience: hybrid
commands:
  - name: ai
    description: Queue an AI exploration mission for a project
    aliases: [ia]
    usage: |
      /ai [project] [focus context]
      /ia [project] [focus context]

      Queues a mission that explores a project in depth via a dedicated
      CLI runner (app.ai_runner) and suggests creative improvements.
      Runs as a full agent mission with access to the codebase.

      Optional focus context steers the exploration toward a specific
      area or topic, similar to /audit's extra context support.

      Examples:
        /ai                                    — explore a random project
        /ai koan                               — explore the koan project
        /ai koan explore the notification pipeline — focused exploration
        /ia backend look at error handling      — explore with focus
handler: handler.py
---
