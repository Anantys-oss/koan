---
type: doc
title: "KOAN.md — koan-only project instructions"
description: "Documents the optional project-root KOAN.md file and the .koan/ directory (a second .koan/KOAN.md, per-skill .koan/skills/<skill>/*.md hooks, and a structured .koan/config.yaml with review.always_check): koan-only steering injected into the autonomous agent's system prompt but never loaded by interactive Claude Code sessions, with precedence rules, the 16k-char cap, and this repo's dogfood layout."
tags: [users]
created: 2026-07-09
updated: 2026-07-22
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

Runner skills pass `project_path` into `load_skill_prompt` /
`load_prompt_or_skill`, so they receive **both** `.koan/skills/<skill>/*`
(per-skill steering) **and** the general `KOAN.md` (root + `.koan/KOAN.md`),
appended in that order — the same always-on guidance the agent loop gets. Core
runners that honor it include `review`, `plan`, `pr`, `fix`, `implement`, and
`rebase` (and their sub-passes). A runner that never passes `project_path`
receives neither until wired.

Everything is opt-in by file existence and a no-op when absent. Prompt-only
skills (no loader) run without a resolved project in scope, so they receive
neither `.koan/skills/` nor general `KOAN.md` — steer those via `CLAUDE.md` or
the mission text instead.

### Seeing what got loaded (`make logs`)

Every steering file koan folds into a prompt is announced on the run log, so you
can confirm the context is actually in play. Watch `make logs` for lines like:

```
[context] Detected KOAN.md, loaded 1240 chars (~ 354 tokens)
[context] Detected .koan/skills/plan, loaded 380 chars (~ 108 tokens)
[context] Detected CLAUDE.md (auto-loaded by CLI), loaded 8900 chars (~ 2542 tokens)
```

`KOAN.md` and `.koan/skills/<skill>` lines mean koan injected that content;
the `CLAUDE.md` line is **detection-only** — Claude Code loads `CLAUDE.md` from
the working directory itself, koan just reports its size so you can see the full
steering context at a glance. Token counts are a `chars/3.5` estimate.

## The `.koan/config.yaml` file

Alongside the markdown steering files, `.koan/` can hold a structured
`config.yaml` — a per-repository configuration surface for koan's behavior on
*your* code. It is distinct from the operator's KOAN_ROOT `instance/config.yaml`
(which configures the koan daemon itself); this file lives in the target repo
and is committed like any other project file.

Everything in it is optional, and the surface is designed to grow more keys over
time. This section documents the one key that ships today.

### `review.always_check` — never skip these files

On a large PR, koan compresses the diff to fit the review budget and may drop
lower-priority files (Markdown, text, config), surfacing them as a
`⚠️ Partial review — N file(s) omitted` note. On a repo that ships skills, docs,
or config-as-code, those are often exactly the files you most want reviewed.

List file globs under `review.always_check` and any changed file whose **path or
basename** matches is *pinned*: included in the reviewed diff ahead of budgeted
files, and never silently dropped while budget remains (so it also won't appear
in the Partial-review note).

```yaml
# <your-repo>/.koan/config.yaml
review:
  always_check:
    - "SKILL.md"      # matches at any depth (basename match)
    - "*.md"          # matches any Markdown file (`*` spans `/`)
    - "docs/api/*"    # matches files under a directory
```

- **Matching** is `fnmatch`-style and **case-sensitive** (`*`, `?`, `[seq]`).
  `*` spans `/`, so `*.md` matches Markdown at any depth; a bare `SKILL.md`
  matches the basename at any depth.
- **Pinning reorders inclusion only** — it does *not* raise the diff-size budget.
  A pinned file larger than the whole budget is still included as far as it fits
  (its first hunks), consistent with how any oversized file is handled.
- **Fail-safe:** an absent, empty, or malformed `.koan/config.yaml` is a no-op —
  the review runs exactly as it would with no file, and a bad config never aborts
  a review. Malformed values for `always_check` are ignored.
- **Precedence & scope:** this only affects files already in the PR diff (it
  protects them from being skipped; it can't add unrelated files). It changes
  neither the review's findings schema nor its prompt wording.

When at least one file is pinned, koan logs a line you can watch on `make logs`:

```
[review] Pinned 3 file(s) via .koan review.always_check: plugins/x/SKILL.md, README.md, docs/api/spec.md
```

### Sample config & future keys

A full annotated sample lives at
[`docs/reference/koan-config.sample.yaml`](../reference/koan-config.sample.yaml).
Copy it into your repo's `.koan/` directory and keep only what you need. It also
shows, as commented-out examples, repo-level review knobs planned for the
extensible config surface (not yet implemented):

- **`review.never_check`** — the inverse of `always_check`: globs whose matching
  files are intentionally skipped (generated code, vendored deps, lockfiles).
- **`review.pause_label`** — a per-repo override of the review pause label.
- **`review.default_focus`** — focus passes that always run for this repo.
- **`review.compressor_token_budget`** — a per-repo diff-size budget override.

The contract for this behavior is documented in
`specs/components/skills.md` ("`review` diff-size & partial-coverage contract"
and "Repo config file (`.koan/config.yaml`)").

## Example: this repository (dogfood)

The Kōan source tree ships its own steering so autonomous missions on koan
itself apply repo-specific quality gates:

```
KOAN.md                              # thin always-on priorities
.koan/skills/
  review/quality-gates.md
  fix/quality-gates.md
  implement/quality-gates.md
  rebase/quality-gates.md
  plan/quality-gates.md
  pr/quality-gates.md
```

Content is intentionally short: unique failure modes (specs discipline,
privacy, `KOAN_ROOT` / mock boundaries, OpenAPI, skill docs) — not a copy of
`CLAUDE.md`. Keep fragments under the 16k per-skill cap; prefer one
`quality-gates.md` per skill.

**Gitignore note:** runtime signal files (`.koan-status`, `.koan-stop`, …)
stay ignored via `.koan-*`. The project directory `.koan/` is **not** ignored
so skill hooks can be committed like any other project file.

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

See also the committed `KOAN.md` and `.koan/skills/*/quality-gates.md` at the
root of this repository for a full dogfood example.
