---
title: Mission Decomposition
description: Split complex missions into an ordered sequence of focused sub-missions via a lightweight LLM classifier.
tags: [missions, agent-loop, users]
---

# Mission Decomposition

Some missions span several unrelated phases — "refactor module X, add feature Y,
then update the docs." Kōan can split such a mission into an ordered sequence of
focused sub-missions so each phase gets its own branch, its own PR, and
independent failure recovery. Splitting is driven by a lightweight LLM classifier
(Haiku by default) rather than heuristics.

Decomposition is **off by default**. Only natural-language missions are eligible;
`/command` (skill) missions are never decomposed.

## Enabling it

Configuration lives in `instance/config.yaml` under `decompose:`:

```yaml
decompose:
  enabled: false    # Master switch. The [decompose] tag still works when false.
  auto: false       # When true, ALL missions are classified (no tag needed).
                    # Requires enabled: true. Adds a Haiku call per mission.
```

There are three effective modes:

| `enabled` | `auto` | Behavior |
|-----------|--------|----------|
| `false`   | (any)  | Only missions **explicitly tagged `[decompose]`** are classified. Auto-decompose is off. |
| `true`    | `false`| The `[decompose]` tag is honored; untagged missions run whole. |
| `true`    | `true` | **Every** eligible mission is classified — no tag needed. Costs one Haiku call per mission. |

The `[decompose]` tag always works regardless of `enabled`, so you can opt a
single mission into decomposition without turning the feature on globally:

```
Refactor the auth layer, add rate limiting, and document the new endpoints [decompose]
```

## What the classifier decides

The classifier returns **atomic** (run the mission as-is) or **composite** (split
into sub-tasks). It is deliberately conservative — when in doubt it chooses
atomic. A mission is only split when it involves three or more distinct
deliverables, spans multiple unrelated subsystems, or mixes concerns that benefit
from separate PR visibility. At most six sub-tasks are produced; a longer list is
truncated.

If the classifier call fails or returns malformed output, the mission runs
**whole** — decomposition never blocks a mission from executing.

## Mission tags and lifecycle

Decomposition uses three tags in the mission store (shown in `missions.md`), all
applied automatically:

- **`[decompose]`** — user-applied request tag (or implicit when `auto: true`).
- **`[group:ID]`** — each injected sub-mission carries the shared group ID.
- **`[decomposed:ID]`** — the original parent mission, kept in Pending but
  skipped by the mission picker while its sub-tasks run.

The lifecycle:

1. A mission tagged `[decompose]` (or any mission when `auto: true`) reaches the
   **decomposition gate** (Step 4d of the agent loop). The classifier runs.
2. **Atomic** → the mission runs normally.
3. **Composite** → the sub-tasks are injected into Pending, each tagged
   `[group:ID]`, in order. The parent is retagged `[decomposed:ID]` and left in
   Pending, where the picker skips it. The split is recorded in the daily
   journal.
4. Sub-tasks are picked and executed like any other mission — each produces its
   own branch and PR.
5. A **group-completion sweep** (Step 4c) runs every loop iteration. Once no
   `[group:ID]` sub-mission for a parent remains in Pending or In-Progress, the
   parent is transitioned out of Pending:
   - **auto-completed** if at least one sub-task succeeded, or
   - **auto-failed** if *all* sub-tasks failed.

The sweep short-circuits cheaply when no `[decomposed:` parent exists in Pending
(the common case for installs with decomposition off), so it adds no meaningful
cost when unused. Both the sweep and the classifier gate are suppressed under
passive (read-only) mode.

### Stuck-parent alerting

If a parent is ready to transition but the sweep repeatedly cannot complete or
fail it (for example, its line no longer matches), the failure is counted. After
five consecutive failed sweeps Kōan sends a Telegram warning via the outbox so a
parent stuck in Pending is surfaced rather than silently looping forever.

## Cost and defaults

Each classification is a single low-cost model call (`models.lightweight`,
defaulting to Haiku, with the configured fallback). With `auto: true` that is one
extra call per mission — enable it only if the phase-splitting is worth the added
latency and quota. With `auto: false`, the cost is paid only for missions you
explicitly tag `[decompose]`.

## Related

- [Model Configuration](model-configuration.md) — the `models.lightweight` model
  used by the classifier.
- [User Manual](user-manual.md) — full command and workflow reference.
