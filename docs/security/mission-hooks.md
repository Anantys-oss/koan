---
type: doc
title: "Mission Hooks Security"
description: "Threat model and safe-use guidance for repo-config-driven pre/post shell hooks (.koan/config.yaml pre_hooks/post_hooks): why they execute arbitrary code from the target repo, the default-off operator opt-in gate and per-project override that contain the risk, and the audit surface."
tags: [security]
created: 2026-07-26
updated: 2026-07-26
---

# Mission Hooks Security

Kōan can run shell commands a repo declares in its own
`.koan/config.yaml` under `pre_hooks` / `post_hooks`, before and after a mission
(see [koan-md.md](../users/koan-md.md) → "Mission hooks" and
`specs/components/agent-loop.md` → "Mission hooks"). This page is the threat
model for that capability.

## The risk

Everything else Kōan reads from a target repo's `.koan/config.yaml` is *data* —
glob patterns, labels, budgets. Mission hooks are different: they are **arbitrary
shell commands that Kōan executes on the operator's host**, sourced from a file
that anyone who can land a commit on the repo's working branch can edit.

That makes mission hooks a **remote-code-execution surface**:

- A malicious or compromised contributor could add `pre_hooks: ["curl … | sh"]`
  to `.koan/config.yaml` and have it run the next time Kōan works that repo.
- The commands run with the **full privileges of the Kōan agent process** — the
  same trust level as the existing Python lifecycle hooks
  ([hooks.md](../architecture/hooks.md)) and the CLI subprocess itself, including
  access to whatever credentials that process can reach.

This is materially more dangerous than `review.always_check`, which can only
*reorder* which already-in-diff files a review looks at.

## The control: default-off operator opt-in

The load-bearing mitigation is that **mission hooks do not run at all unless the
operator explicitly enables them.** A repo's `pre_hooks`/`post_hooks` are inert by
default; Kōan logs a one-line "skipped (not enabled)" note and moves on.

- **Global gate** — the operator sets, in their **own** `KOAN_ROOT`
  `instance/config.yaml` (never in a target repo):

  ```yaml
  mission_hooks:
    enabled: true      # default false
  ```

- **Per-project override** — even with the global gate on, the operator can
  disable hooks for a specific project in `projects.yaml`
  (`mission_hooks: false`), or enable only specific projects
  (`mission_hooks: true`) while leaving the global gate off.

Resolution: a per-project value wins when set; otherwise the global setting;
otherwise disabled. This is the same opt-in shape as `review_dispatch` /
`ci_dispatch`.

Because the operator chooses which repositories enter `projects.yaml` in the
first place, enabling mission hooks is a deliberate, per-repo grant of "run this
repo's setup/teardown shell on my host" — not an ambient capability.

## Defense in depth

Even once enabled, execution is bounded and audited:

- **Bounded** — each command has a wall-clock timeout (`MISSION_HOOK_TIMEOUT`,
  default 300s); the number of commands per list and each command's length are
  capped by the parser (`project_koan.get_mission_hooks`). A pathological config
  cannot hang the loop or flood the logs.
- **Isolated** — each command is error-isolated: a non-zero exit, a timeout, or a
  launch failure is logged and skipped; it never aborts the mission, blocks later
  commands, or crashes the daemon (best-effort, matching the lifecycle-hook
  philosophy).
- **Audited** — every hook's start, exit code, duration, timeout, and (bounded)
  output is written to the run log (`make logs`) under the `[mission_hooks]`
  prefix, so the operator can see exactly what ran.
- **Scoped env** — commands run in the target repo's working directory with
  `KOAN_MISSION_TYPE` and (post only) `KOAN_MISSION_STATUS` exported. No mission
  secrets are injected beyond the inherited process environment.

## Safe-use guidance

- Enable the global gate only if you trust *every* repo in `projects.yaml`, or
  prefer per-project enablement for the specific repos that need it.
- Treat `.koan/config.yaml` hook changes in a repo's PRs as security-relevant —
  review them like you would a CI config change.
- Keep hook commands idempotent and fast; prefer calling a checked-in script
  (`./scripts/koan-setup.sh`) over inline one-liners so the logic is itself
  reviewable in the repo.
- Run Kōan as an unprivileged user; the RCE blast radius is whatever that
  process can reach.

## Relationship to the disalignment threat model

Mission hooks widen the "operator-authorized automation" surface analyzed in
[threat-model-agent-disalignment.md](./threat-model-agent-disalignment.md): they
are shell the operator opted into, gated and audited, not autonomous agent
action. Human PR review of `.koan/config.yaml` changes remains the primary
control, consistent with that model.
