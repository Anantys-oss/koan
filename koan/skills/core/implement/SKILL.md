---
name: implement
scope: core
group: code
emoji: 🔨
description: "Implement a tracker issue (GitHub or Jira)"
version: 1.0.0
audience: hybrid
caveman: true
model_key: mission
github_enabled: true
github_context_aware: true
commands:
  - name: implement
    description: "Queue an implementation mission for a GitHub or Jira issue"
    usage: "/implement <issue-url> [additional context]"
    aliases: [impl]
handler: handler.py
---
