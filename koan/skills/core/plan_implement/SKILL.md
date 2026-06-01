---
name: plan_implement
scope: core
group: code
emoji: 🧠🔨
description: "Queue a plan then implement combo for an issue (ex: /planit https://github.com/owner/repo/issues/42)"
version: 1.0.0
audience: hybrid
github_enabled: true
github_context_aware: true
sub_commands: [plan, implement]
commands:
  - name: planimplement
    description: "Queue /plan then /implement for an issue — plan insights feed the implementation"
    usage: "/planimplement <issue-url>"
    aliases: [planimp, planimpl, planit, plandoit]
handler: handler.py
---
