---
type: doc
title: "KOAN.md — koan-only project instructions"
description: "Documents the optional project-root KOAN.md file and the .koan/ directory (a second .koan/KOAN.md, per-skill .koan/skills/<skill>/*.md hooks, and a structured .koan/config.yaml with review.always_check and hooks.<event>): koan-only steering injected into the autonomous agent's system prompt but never loaded by interactive Claude Code sessions, with precedence rules, the 16k-char cap, and this repo's dogfood layout."
tags: [users]
created: 2026-07-09
updated: 2026-09-04
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
time. This section documents the keys that ship today.

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

### `hooks.<event>` — run your own skills after a koan event

Name Claude Code skills to run when one of koan's lifecycle events fires, and
koan queues a mission for each. Keys are the event names — `session_start`,
`session_end`, `pre_mission`, `post_mission`, `post_review` — and values are
lists of skill names:

```yaml
# <your-repo>/.koan/config.yaml
hooks:
  post_review:
    - docs-refresh
```

The example runs your `docs-refresh` skill after koan posts a review,
receiving the PR it just reviewed.

**Why this exists.** A `/review` runs read-only on purpose: no `Skill` tool, no
subagents, no MCP, and your repo's `.claude/` settings are not loaded. That is
right for reviewing untrusted code, but it means a review pass cannot do
follow-up work that needs real tooling. Naming a skill here moves that work onto
the mission loop, which *does* load your `.claude/skills`, can invoke skills,
and is not MCP-stripped.

- **Read from the operator's checkout, not the PR.** A review runs against a
  detached worktree of the *pull request head*, so a `.koan/config.yaml` in it
  is whatever the contributor pushed. koan therefore reads `hooks.<event>` from
  the project checkout the operator registered — a PR cannot add or change the
  skills that run. A project koan does not have registered is a no-op.
- **Read from your default branch, as committed.** Within that checkout, koan
  reads the file from the default branch of its `origin` (`git show
  origin/main:.koan/config.yaml`, in effect), not from whatever the working tree
  currently holds — koan itself checks pull-request branches out there when it
  rebases one, and a killed run can leave one parked. So an edit to this key
  takes effect once it is **merged and fetched**, not while it sits on a branch
  or uncommitted. Two exceptions where the working tree is used instead, because
  nothing external can land in it: a directory that is not a git repo, and a
  repo with no remote at all. If the default branch cannot be resolved (several
  remotes and none named `origin`), koan reads nothing and logs a warning.
- **Queued, not run inline.** Handlers execute inside the process that fired the
  event, and a skill pipeline can take minutes. Your skill runs as a normal
  pending mission shortly afterwards — watch `instance/missions.md` or
  `make logs`.
- **Names only.** A name must match `^[a-z0-9][a-z0-9-]*$` (max 64 chars, 10
  skills per event); anything else is dropped with a warning. koan writes the
  mission sentence itself. This is deliberate — the value reaches a
  *write-capable* agent, so free text is refused rather than cleaned up.
- **Not queued twice.** Re-firing the same event for the same subject (the PR
  URL, or the mission title) does not queue the work again *while the earlier
  mission is still pending or in progress*. Once it has completed, a later
  re-fire — a new push to the same PR, say — queues it again. A different PR
  queues separately.
- **No runaway loop.** A skill you queue under `pre_mission` or `post_mission`
  does not re-trigger itself: the mission koan queues is marked, and that mark
  stops its own lifecycle events from queuing the skill again.
- **Events without a project** — `session_start` and `session_end` carry no
  `project_path`, so they are a no-op here.
- **Events without a subject** — koan's own autonomous and contemplative
  iterations fire `pre_mission`/`post_mission` with no mission title and no PR,
  and those queue nothing. The subject is what "not queued twice" keys on, so an
  event without one would append a fresh copy every iteration. `post_mission`
  after a real mission, and `post_review` after a review, always carry one.
- **Fail-safe:** a malformed config is ignored and never disturbs the event.

Requires the named skill to be resolvable by Claude Code in your repo — most
commonly `<your-repo>/.claude/skills/<name>/SKILL.md`.

### Mission hooks — run shell before/after a mission

`pre_hooks` and `post_hooks` let your repo run shell commands **around** any
mission Kōan performs on it — install dependencies, start a service, warm a
cache before; stop the service or report status after. They are keyed by
mission type, with a `default` fallback:

```yaml
# <your-repo>/.koan/config.yaml
default:
  pre_hooks:
    - "echo setting up"        # runs before every mission type…
  post_hooks:
    - "echo done"              # …unless that type overrides the phase

review:
  pre_hooks:
    - "docker compose up -d db"
    - "./scripts/wait-for-db.sh"
  post_hooks:
    - 'echo "review finished: $KOAN_MISSION_STATUS"'

fix:      { pre_hooks: ["npm ci"] }   # applies to fix on both PRs and issues
implement:{ pre_hooks: ["make deps"], post_hooks: ["make clean"] }
```

Well-known mission types: `review`, `fix`, `plan`, `rebase`, `refactor`,
`implement` (also `recreate`, `squash`, and any other Kōan command name).

**How they run:**

- **Order.** Commands in a list run top-to-bottom, one after another, in your
  repo's working directory.
- **Precedence — replace, per phase.** If a mission-type section defines a list
  for a phase, that list is used *instead of* `default` for that phase (they are
  not merged). `pre` and `post` are resolved independently — a type can override
  `pre_hooks` while still inheriting `default.post_hooks`.
- **post_hooks always run** — on success *and* failure. The command environment
  carries `KOAN_MISSION_STATUS` (`success` / `failure`) and `KOAN_MISSION_TYPE`
  (e.g. `review`), so one post-hook can branch on the outcome. (`pre_hooks` see
  `KOAN_MISSION_TYPE` but no status yet.)
- **Best-effort.** A hook that fails, cannot start, or exceeds its timeout is
  logged (`make logs`) and skipped — it never aborts the mission or crashes koan.
- **Bounded.** Each command has a wall-clock timeout; the number of commands per
  list and each command's length are capped.

> **⚠️ Security — disabled by default.** Because these commands come from a file
> **inside the target repo**, running them is arbitrary code execution on the
> operator's host. Kōan therefore **ignores `pre_hooks`/`post_hooks` unless the
> operator has explicitly opted in.** The operator enables them in their
> `KOAN_ROOT` `instance/config.yaml`:
>
> ```yaml
> mission_hooks:
>   enabled: true       # default false
> ```
>
> and may override per project in `projects.yaml` (`mission_hooks: true|false`).
> When disabled, any hooks in a repo config are skipped with a one-line log
> note. See [Mission hooks security](../security/mission-hooks.md).

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
