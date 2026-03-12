---
name: scaffold-skill
scope: core
description: Generate a new skill from a description
version: 1.0.0
audience: bridge
group: system
worker: true
commands:
  - name: scaffold-skill
    description: Generate SKILL.md + handler.py for a new custom skill
    usage: /scaffold-skill <scope> <name> <description>
    aliases: [scaffold, new-skill]
handler: handler.py
---
