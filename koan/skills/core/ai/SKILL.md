---
name: ai
scope: core
group: ideas
emoji: ✨
description: Queue an AI exploration mission for a project
version: 1.2.0
audience: hybrid
commands:
  - name: ai
    description: Queue an AI exploration mission for a project
    aliases: [ia]
    usage: |
      /ai [project] [focus context] [--issues]
      /ia [project] [focus context] [--issues]

      Queues a mission that explores a project in depth via a dedicated
      CLI runner (app.ai_runner) and suggests creative improvements.
      Runs as a full agent mission with access to the codebase.

      Optional focus context steers the exploration toward a specific
      area or topic, similar to /audit's extra context support.

      --issues: Create GitHub issues for high/medium impact findings
      (up to 5 per run). Issues are deduplicated across runs.

      Examples:
        /ai                                    — explore a random project
        /ai koan                               — explore the koan project
        /ai koan explore the notification pipeline — focused exploration
        /ai koan --issues                      — explore and create GitHub issues
        /ia backend look at error handling      — explore with focus
handler: handler.py
---
