---
type: skill-spec
title: "Skill Spec — recreate"
description: "Documents the `/recreate` skill that rebuilds a too-far-diverged PR from scratch on current upstream via a fresh branch and reimplementation, rather than rebasing."
tags: [skill]
created: 2026-06-27
updated: 2026-06-27
---

# Skill Spec — `recreate`

## Command(s)

- **Primary:** `/recreate <pr-url>`
- **Aliases:** `rc`
- **Group:** `pr`

## Purpose

Recreate a diverged PR from scratch on current upstream: fetch the original metadata and
diff, branch fresh from base, and reimplement. Used when a PR has drifted too far to
rebase cleanly.

See `docs/users/skills.md` for the end-user `/recreate` reference and
`docs/users/user-manual.md` for the fuller walkthrough.

## Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
| PR URL | command arg | yes | parsed by `github_url_parser` |
| trailing context | command arg | no | extra guidance |

## Outputs / side effects

- Queues a recreate mission (`model_key: mission`); runs via `recreate_pr.py`.
- Creates a new `<prefix>/*` branch and a fresh draft PR reimplementing the intent.

## Error cases

| Condition | Behavior |
|---|---|
| invalid PR URL | reply with usage |
| original PR unreachable | abort with notice |

## Integration hooks

- **Handler:** `handler.py`. **GitHub:** `github_enabled` + `github_context_aware`.
- **Runner:** `recreate_pr.py` (fetch metadata/diff → fresh branch → reimplement).

## Invariants

- Reimplements from scratch on current base — does not cherry-pick the stale branch.
- Always a fresh draft PR; the diverged original is left for the human to close.

## Known debt / watch-outs

- Reimplementation can diverge from the original author's intent — the fetched
  metadata/diff is context, not a spec; PR description should note it's a recreate.
