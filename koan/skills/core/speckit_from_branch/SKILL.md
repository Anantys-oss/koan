---
name: speckit_from_branch
scope: core
group: code
emoji: 🌿
description: "Resume spec-kit from a human-validated spec on a branch (skip specify, run plan -> tasks -> implement -> review -> CI -> PR)"
version: 1.0.0
audience: hybrid
model_key: mission
github_enabled: true
github_context_aware: true
commands:
  - name: speckit_from_branch
    description: "Run spec-kit plan onward from a human-authored spec already pushed to a branch"
    usage: "/speckit_from_branch <repo-id> <branch-name>"
handler: handler.py
---

Queue a `/speckit_from_branch` mission: the handler resolves the project from
`repo-id`, gates on its spec-kit constitution, and queues a single mission to
resume the spec-kit pipeline (`plan -> tasks -> implement -> review -> CI -> PR`)
from a spec a human has already pushed to `branch-name` (skipping `specify`).
The dedicated runner (specify-skip + branch-off git flow) is the remaining US5
piece — see `specs/001-speckit-native-support/tasks.md`.
