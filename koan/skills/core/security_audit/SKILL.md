---
name: security_audit
scope: core
group: code
emoji: 🛡️
description: Security-focused audit of a project codebase — finds up to 5 critical vulnerabilities and creates GitHub issues
version: 1.0.0
audience: hybrid
github_enabled: true
github_context_aware: true
commands:
  - name: security_audit
    description: SDLC security audit — finds critical vulnerabilities and creates GitHub issues for each
    usage: /security_audit <project-name> [extra context] [limit=N]
    aliases: [security, secu]
handler: handler.py
worker: true
---
