---
type: doc
title: "KOAN.md — koan-only project instructions"
tags: [users]
created: 2026-07-09
updated: 2026-07-09
---

# KOAN.md — koan-only project instructions

`KOAN.md` is an optional file at a project's root that gives instructions to
the autonomous Kōan agent **only**. It has the same format as `CLAUDE.md`, but
interactive Claude Code sessions never load it — so you can steer koan's
autonomous work without changing the shared `CLAUDE.md` your whole team sees.

## How it works

On every mission, Kōan reads `<project>/KOAN.md` (if present) and injects its
content into the agent's system prompt, framed as authoritative project
guidance. Because Claude Code only auto-loads `CLAUDE.md`, `KOAN.md` stays
invisible to human sessions by construction.

## Precedence

1. The current mission's explicit instructions (highest).
2. `KOAN.md`.
3. `CLAUDE.md` and generic koan defaults (lowest).

## Limits

- Read from the project root only (not nested directories).
- Capped at 16,000 characters; longer files are truncated with a notice.
- Blank/whitespace-only files are ignored.

## Example

```markdown
# KOAN.md
- Prefer documentation and analysis over code changes on this project.
- Always run `make lint` and `make test` before opening a PR.
- Never touch files under `vendor/`.
```
