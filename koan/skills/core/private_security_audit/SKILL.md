---
name: private_security_audit
scope: core
group: code
emoji: 🔒
description: Local-only security audit — findings stay in the journal, never posted to GitHub
version: 1.0.0
audience: hybrid
caveman: false
github_enabled: false
commands:
  - name: private_security_audit
    description: Security audit whose findings are written to the journal only (no GitHub issues, no PVRS)
    usage: /private_security_audit <project-name> [extra context] [limit=N]
    aliases: [private_security, psecu]
handler: handler.py
worker: true
---
