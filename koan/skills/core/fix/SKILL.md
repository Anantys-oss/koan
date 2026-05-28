---
name: fix
scope: core
group: code
emoji: 🐞
description: "Fix a tracker issue end-to-end, or batch-queue all open GitHub issues from a repo"
version: 1.1.0
audience: hybrid
caveman: true
github_enabled: true
github_context_aware: true
commands:
  - name: fix
    description: "Queue a fix mission for a GitHub or Jira issue — understand, plan, test, implement, and submit a PR. Can also batch-queue all open GitHub issues from a repo URL. Use --now to queue at the top."
    usage: "/fix [--now] <issue-url> [additional context] OR /fix <repo-url> [--limit=N]"
handler: handler.py
---
