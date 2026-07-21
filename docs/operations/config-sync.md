---
type: doc
title: "Real-time Config Sync"
description: "Documents real-time config sync: the dashboard reflects config.yaml/projects.yaml edits within ~2s over the existing SSE, classifying safe hot-reload keys vs restart-required changes and gating restarts on agent idleness."
tags: [operations]
created: 2026-07-17
updated: 2026-07-17
---

# Real-time config sync

Kōan watches `instance/config.yaml` and `instance/projects.yaml` for changes
and reflects them in the dashboard within ~2s (via the existing
`/api/state/stream` SSE — no extra dependency or watcher thread).

## Safe vs. restart-required

- **Safe (hot, no restart):** `dashboard.nickname`, `tools.*`,
  `automation_rules.*`, `messaging_level`, `verbose`. These are re-read per
  use, so edits take effect immediately.
- **Restart required:** `cli_provider`, `models`, anything in
  `projects.yaml` (paths/providers), and any key not on the safe allowlist.

The allowlist is closed: anything not explicitly listed is treated as
restart-required, and `projects.yaml` is restart-required wholesale. This is
deliberate — misclassifying an unsafe key as safe would silently run the agent
on stale state. The allowlist lives in `config.py::_HOT_RELOAD_SAFE_KEYS`.

## How it works

The agent records a baseline snapshot at startup
(`instance/.koan-config-baseline.json`). The dashboard diffs the current files
against it:

- Safe changes toast **"Settings updated"** and the badge stays green
  **"Synced"**.
- Unsafe changes turn the badge yellow **"Restart pending"** and show a
  **"Restart required"** modal listing the changed keys.

The restart only fires when the agent is **idle** (no in-flight mission). The
modal's "Restart now" button posts to `POST /api/config/restart-if-idle`, which
returns `409` when the agent is busy, guarding against lost work. After a real
restart the baseline is rewritten, so the badge flips back to green.

Because detection is folded into the existing 2s SSE tick, multiple rapid edits
coalesce into a single emitted state — no separate debounce is needed.

## Endpoints

- `GET /api/config/sync` — non-SSE poll fallback returning the same status block.
- `POST /api/config/restart-if-idle` — idle-gated restart (`200` when idle,
  `409` when working).

## Disabling

Set `config_sync: { enabled: false }` in `config.yaml`.
