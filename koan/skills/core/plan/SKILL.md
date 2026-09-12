---
name: plan
scope: core
group: code
emoji: 🧠
description: Deep-think an idea and create a tracker issue with a structured plan
version: 2.1.0
audience: hybrid
api_exposed: true
caveman: false
github_enabled: true
github_context_aware: true
commands:
  - name: plan
    description: Plan an idea or iterate on an existing tracker issue
    usage: /plan [--iterations N] <idea>, /plan <project> <idea>, /plan <issue-url>
handler: handler.py
---

## Options

- `--iterations N` (1-5, default 1): Run N rounds of plan generation. After the initial plan, a critic identifies content gaps and contradictions, then the plan is regenerated with that feedback. Only the final iteration is posted. Cost scales linearly — `--iterations 3` uses ~5× the tokens of a standard plan call.
