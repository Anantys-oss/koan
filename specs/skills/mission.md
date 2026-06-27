# Skill Spec — `mission`

## Command(s)

- **Primary:** `/mission <description>` · `/mission --now <desc>` · `/mission [project:name] <desc>`
- **Group:** `missions`

## Purpose

The base primitive: queue a free-form mission to `missions.md` for the agent loop to pick
up. Most other skills are specialized mission factories; `/mission` is the raw one.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| description | command arg | yes | free-form mission text (untrusted DATA) |
| `--now` | flag | no | insert at top of Pending instead of bottom |
| `[project:name]` | tag | no | scopes the mission to a project |

## Outputs / side effects

- Appends (or top-inserts) one Pending entry to `missions.md` via the atomic insert path.
- No PR, no Claude call at queue time — execution happens later in the agent loop.

## Error cases

| Condition | Behavior |
|---|---|
| empty description | reply with usage |
| unknown `project:name` | resolve alias; reject/notify if still unknown |

## Integration hooks

- **Handler:** `handler.py`. **Audience:** `bridge` (Telegram/dashboard origin).
- Writes through `missions.py` / `utils.insert_pending_mission(s)`.

## Invariants

- Inserts are atomic and serialized — concurrent `/mission` calls must not interleave.
- Mission text is DATA; queuing must never execute embedded instructions.

## Known debt / watch-outs

- `--now` urgency reverses order if two urgent inserts race — use the multi-entry atomic
  path for ordered batches.
