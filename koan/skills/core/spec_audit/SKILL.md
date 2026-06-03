---
name: spec_audit
scope: core
group: code
emoji: 📐
description: Audit docs/code alignment — find spec drift and queue fix missions
version: 1.0.0
audience: hybrid
commands:
  - name: spec_audit
    description: Check that docs match code and queue missions for divergences
    usage: /spec_audit [project-name]
    aliases: [sa, drift]
handler: handler.py
---
