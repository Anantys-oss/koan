---
type: doc
title: "KOAN.md — koan-only project instructions"
tags: [users]
created: 2026-07-09
updated: 2026-07-12
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

- Read from `KOAN.md` at the project root **and** `.koan/KOAN.md` (both are
  concatenated); nested directories other than `.koan/` are not scanned.
- Capped at 16,000 characters (combined); longer content is truncated with a notice.
- Blank/whitespace-only files are ignored.

## The `.koan/` directory

For finer control, a project can add an optional `.koan/` directory (checked
into the target repo):

```
myrepo/
├── KOAN.md                       # general — root, unchanged
└── .koan/                        # optional
    ├── KOAN.md                   # general — same role as root KOAN.md
    └── skills/
        ├── review/
        │   └── extra-rules.md    # appended to the /review prompt
        └── plan/
            └── house-style.md    # appended to the /plan prompt
```

- **`.koan/KOAN.md`** — a second source for general koan-only guidance,
  concatenated after the root `KOAN.md`.
- **`.koan/skills/<skill>/*.md`** — extra instructions appended (append-only)
  to that core skill's built-in prompt, for runner-based skills (`review`,
  `refactor`, `plan`, …). All `*.md` files in the directory are concatenated in
  filename order and appended to **every** pass of that skill (e.g. `review`'s
  first-pass, reflection, and triage sub-passes all honor `.koan/skills/review/`).
  Per-skill content is capped at 16,000 characters.

`<skill>` is the **invoking skill's** name, not the prompt name. In particular
the `/pr` handler drives its feedback, refactor, and quality-review sub-passes
under a single `pr` skill, so steer all three via `.koan/skills/pr/` (there is
no separate `.koan/skills/refactor/`).

Everything is opt-in by file existence and a no-op when absent. Prompt-only
skills do not read `.koan/skills/`; steer those with general `KOAN.md`.

## Discoverability

Kōan advertises this feature once, unprompted: the first idle period after the
feature ships, it sends a one-time 💡 hint (same format as skill tips) linking
back to this page. The notice is tracked in `instance/.feature-notices.json` and
never repeats.

## Example

```markdown
# KOAN.md
- Prefer documentation and analysis over code changes on this project.
- Always run `make lint` and `make test` before opening a PR.
- Never touch files under `vendor/`.
```
