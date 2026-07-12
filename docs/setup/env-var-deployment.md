---
type: doc
title: "Environment-variable-only deployment"
description: "Explains how Koan can run purely from injected environment variables (Railway/Docker/Kubernetes/systemd) without a hand-authored `.env` file, and the precedence rules between env vars and the synthesized `.env`."
tags: [setup]
created: 2026-06-24
updated: 2026-07-08
---

# Environment-variable-only deployment

Kōan does not require a hand-written `.env` file. On platforms that inject
configuration as process environment variables — Railway, Docker, Kubernetes,
systemd `Environment=` — you can run Kōan without ever authoring a `.env`.

## How it works

When the required `KOAN_*` settings are present in the process environment, the
onboarding `Initialize instance` step does not fail on a missing `env.example`.
Instead `create_env_file()` synthesizes a `.env` from the environment
(`write_env_from_environment()`), mirroring the platform variables into a
`0600` file, and proceeds. `load_dotenv()` then layers that file on top of
`os.environ` using `setdefault`, so injected environment variables always take
precedence.

The "required config present" check is `app.railway.required_env_present()` —
satisfied when an auth token (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`),
a GitHub token (`KOAN_GH_TOKEN`/`GH_TOKEN`), and the Telegram pair
(`KOAN_TELEGRAM_TOKEN` + `KOAN_TELEGRAM_CHAT_ID`) are all in the environment.
When neither a template nor the required env vars are available,
`create_env_file()` returns `False` and onboarding still fails loudly rather
than masking a misconfiguration.

`env.example` remains a template for interactive local setup and is optional in
containerized / PaaS deploys.

## Precedence

1. Process environment variables (highest priority — always win).
2. Values in the synthesized `.env` (only fill gaps via `os.environ.setdefault`).

This means you can set everything via environment variables and let onboarding
mirror them into `.env` for you.

## Instance hydration (boot as *your* instance)

A fresh deploy with an empty volume has no operator state — only the bundled
`instance.example/` template. Set **`KOAN_INSTANCE_REPO`** to your PRIVATE
instance repository and, on cold boot, the entrypoint clones it (including
`.git`) into `instance/`, so Kōan boots as *your* instance: soul, projects,
skills, memory, journal, and any in-flight mission state.

| Variable | Purpose |
|---|---|
| `KOAN_INSTANCE_REPO` | `gh` slug (`owner/koan-instance`) or full git URL of your private instance repo. Unset → seed from `instance.example/`. |
| `KOAN_INSTANCE_REPO_BRANCH` | Optional branch to clone. Default: the repo's default branch. |
| `KOAN_INSTANCE_SYNC_INTERVAL` | Optional seconds between background `git pull --rebase --autostash` of `instance/`. `0` (default) disables it. |

### Full-mirror model & push-back

Hydration restores the **whole** tree, not just config. This works because the
running agent already commits **and pushes** the entire `instance/` directory
via `mission_runner.commit_instance()` on every lifecycle beat (mission
done/failed, session end, quota exhaustion, pause). So the clone is the only
new piece: clone on cold boot, and the agent keeps the remote in sync from
then on. A later redeploy re-clones the up-to-date remote and resumes where it
left off.

### Requirements & caveats

- **Private repo access.** On Railway/GitHub the `gh` clone path uses
  `GH_TOKEN`/`KOAN_GH_TOKEN` directly — no extra setup. The `git clone`
  fallback (self-hosted or non-GitHub URLs) needs a credential helper; on
  Railway `gh auth setup-git` already runs in the entrypoint.
- **Fail-open.** Any clone error falls back to the `instance.example/`
  template — boot never hard-fails.
- **Don't edit the remote by hand while the agent runs.** A direct push to the
  remote makes the agent's next `commit_instance` push non-fast-forward and the
  two diverge permanently. If you must, enable `KOAN_INSTANCE_SYNC_INTERVAL`
  (e.g. `900`) so a throttled `pull --rebase --autostash` reconciles before the
  next push.
- **Secrets stay out of the repo.** `.env` lives at `$KOAN_ROOT/.env`, outside
  `instance/`, and is not tracked.
