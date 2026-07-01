---
name: speckit
scope: core
group: code
emoji: 📋
description: "Run a spec-kit pipeline (specify -> plan -> tasks -> implement) for a goal or tracker issue, then best-effort review/CI and a draft PR"
version: 1.0.0
audience: hybrid
model_key: mission
github_enabled: true
github_context_aware: true
commands:
  - name: speckit
    description: "Run the spec-kit SDD pipeline for a project goal or tracker issue"
    usage: "/speckit <project> <goal> | /speckit <issue-url> [repo:.. branch:..]"
handler: handler.py
---

Queue a `/speckit` mission: the handler gates on the target project's spec-kit
constitution (`.specify/memory/constitution.md`) and queues a single mission. The
agent loop then runs `speckit_runner`, which drives the specify → plan → tasks →
implement pipeline (committing once per task), a best-effort private review loop,
CI/test validation, and a draft PR. See
`specs/001-speckit-native-support/plan.md`.
