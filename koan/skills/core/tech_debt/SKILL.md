---
name: tech-debt
scope: core
group: code
description: Scan a project for tech debt and queue improvement missions
version: 1.0.0
audience: hybrid
commands:
  - name: tech-debt
    description: Scan a project for duplicated code, complex functions, testing gaps, and infrastructure issues
    usage: /tech-debt [project-name]
    aliases: [td, debt]
handler: handler.py
---
