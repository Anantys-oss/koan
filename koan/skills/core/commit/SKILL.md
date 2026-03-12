---
name: commit
scope: core
group: code
description: "Queue a commit mission to stage and commit changes with a conventional message"
version: 1.0.0
audience: hybrid
commands:
  - name: commit
    description: "Queue a mission to analyze changes and create a conventional commit"
    usage: "/commit [message hint]"
    aliases: [ci]
handler: handler.py
---
