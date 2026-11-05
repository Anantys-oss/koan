---
type: doc
title: "Instance Guard"
description: "Documents the two-layer gate that keeps private instance/ state out of git: the pre-push leak gate inside commit_instance and the post-push CI backstop, the shared leak rules, which deployment layouts each layer covers, and what an operator sees when a path is blocked."
tags: [security]
created: 2026-11-05
updated: 2026-11-05
---

# Instance Guard

Safety boundary for the instance commit/push flow: private runtime state
(`instance/`, the `instance.example/skills/` template subtree, live
`projects.yaml` + backups) must never be git-tracked. The rules are path-based
and live in one pure module, **`app.instance_leak_rules`** (`classify`,
`find_violations`) — stdlib-only, no I/O.

## The two layers

| Layer | Where | What it scans | When it runs |
|---|---|---|---|
| **pre-push** | `mission_runner.commit_instance()` | the staged changes (`git diff --cached --name-status -z`) | every lifecycle beat, in the daemon, before commit+push |
| **post-push** | CI workflow `.github/workflows/instance-guard.yml` → `scripts/instance_guard.py` | all tracked files (`git ls-files -z`) | on push/PR to `main` of the public repo |

Both layers run the *same* `find_violations()` core, so a path the pre-push
gate lets through can never slip past the backstop (and vice versa).

### Pre-push gate

`commit_instance()` runs `git add -A` over the instance dir, then reads the
staged changes and unstages (`git reset --`) any change that would track a
leaking path, so the leak is neither committed nor pushed. Design notes:

- The rules are **imported from the installed package**, never loaded or
  executed out of the instance tree — the instance dir is agent-writable, so
  exec'ing a script from it would be a code-execution path in the daemon.
  This also means the gate is active in **every deployment layout** (private
  instance repo, instance dir = public repo, template-seeded), not only the
  ones that happen to ship a guard script.
- **Fail closed**: if reading staged changes or unstaging fails, the whole
  commit is blocked.
- Staged changes are read NUL-separated (`-z`) so non-ASCII path names arrive
  unquoted (line-oriented git output C-quotes them and the prefix rules would
  miss a quoted name).
- A staged **deletion** of a leaking path is allowed through: unstaging it
  would resurrect the tracked entry and strand the leak on the remote
  forever. Modifications of pre-existing tracked leaks are still blocked (the
  index entry is restored to its HEAD version).

### Post-push backstop

The CI workflow runs `python3 scripts/instance_guard.py` over all tracked
files and fails the push/PR on any hit. `scripts/instance_guard.py` is a thin
CLI around the same `app.instance_leak_rules` core (it adds the repo's
`koan/` dir to `sys.path` so it runs standalone with stdlib Python only).

## Which layout does what

- **Private instance repo** (`KOAN_INSTANCE_REPO`, the normal deployment): the
  pre-push gate is the only gate on that remote — the post-push workflow runs
  on the public repo, not on the instance remote.
- **Instance dir = public repo**: both layers apply; pre-push stops the leak
  before it is published, the backstop catches anything that bypassed the
  daemon.

## What an operator sees

- A path is blocked pre-push: `commit_instance` prints
  `[commit_instance] Blocked leaking path(s) from commit+push: <paths>` to
  stderr, commits the remaining clean changes (or nothing, when every staged
  path was a leak — it then returns without committing or pushing), and leaves
  the blocked file on disk, untracked.
- A tracked file violates the rules post-push: the 📁 Instance Guard CI job
  fails, listing each violating path, its reason, and the
  `git rm --cached <path>` fix.

## Limits

- The rules are path-based: content *copied* to a non-leaking path passes
  both layers.
- The gate only sees what `commit_instance` stages; files committed by other
  writers are only caught by the post-push backstop (public repo) or not at
  all (private instance remote).
