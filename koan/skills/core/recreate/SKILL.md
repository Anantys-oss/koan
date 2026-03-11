---
name: recreate
scope: core
group: pr
description: "Recreate a diverged PR from scratch (ex: /recreate https://github.com/owner/repo/pull/42)"
version: 1.0.0
audience: hybrid
github_enabled: true
commands:
  - name: recreate
    description: "Recreate a diverged PR from scratch on current upstream (ex: /recreate https://github.com/owner/repo/pull/42)"
    aliases: [rc]
handler: handler.py
---
