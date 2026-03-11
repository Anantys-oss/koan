---
name: gha-audit
scope: core
group: system
description: Scan GitHub Actions workflows for security vulnerabilities
version: 1.0.0
audience: bridge
commands:
  - name: gha-audit
    description: Audit GitHub Actions workflows for security issues
    usage: "/gha-audit [project-name]"
    aliases: [gha]
handler: handler.py
worker: false
---
