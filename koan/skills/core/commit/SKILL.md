---
name: commit
scope: core
group: code
emoji: 📝
description: "Analyze staged/unstaged changes and create a conventional commit"
version: 1.0.0
audience: hybrid
commands:
  - name: commit
    description: "Generate a conventional commit message from git diffs and commit"
    usage: "/commit [project] [message hint]"
    aliases: [cm]
handler: handler.py
---
