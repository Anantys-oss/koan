# Deploy Kōan on Railway — step-by-step HOWTO

A hands-on walkthrough to get a working Kōan instance on [Railway](https://railway.app)
from a fresh service. Everything runs in **one container**, persists on **one volume**,
and survives every re-deploy — driven by a single flag, `KOAN_DEPLOY=railway`.

> For the design rationale (why permissions, `.env` mirroring, and `projects.yaml`
> resolution work the way they do), see [`docs/setup/railway.md`](docs/setup/railway.md).
> This file is the click-by-click recipe.

---

## 0. Prerequisites

You need four secrets ready (you already have these):

| Variable | What it is | Where to get it |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code subscription auth (or use `ANTHROPIC_API_KEY` for API billing) | `claude setup-token` locally, or an `sk-ant-…` API key |
| `GH_TOKEN` | A GitHub PAT for the **bot account** (the identity that opens PRs) | github.com → Settings → Developer settings → Fine-grained / classic PAT with `repo` scope |
| `KOAN_TELEGRAM_TOKEN` | Your Telegram bot token | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `KOAN_TELEGRAM_CHAT_ID` | The chat (you) the bot talks to | message your bot, then read `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates` |

> ⚠️ The **fifth** variable, `KOAN_DEPLOY=railway`, is the one that flips the
> container into hosted mode. Without it the instance boots in "local/dev" mode
> and you hit the permission/onboarding pain this flag exists to remove.

---

## 1. Create the service

1. Railway dashboard → **New Project** (or open an existing one) → **New Service**.
2. **Deploy from GitHub repo** → select your `koan` fork/repo.
3. Pick the branch you want to deploy. To test this branch, choose
   **`koan0/implement-2081`** (Settings → Source → Branch).

Railway builds from the repo's root **`Dockerfile`** automatically — no `railway.json`
or Nixpacks config needed. The image's `ENTRYPOINT` runs `supervisord`, which
launches both the agent loop (`run.py`) and the Telegram bridge (`awake.py`).

---

## 2. Add the persistent volume

This is the single most important step — **only the volume survives re-deploys.**

1. Service → **Settings → Volumes → New Volume**.
2. Mount path: **`/app/instance`** (exactly this path).

Railway mounts the volume as `root:root`. That is fine: when `KOAN_DEPLOY=railway`
is set, the entrypoint **chowns `/app/instance` to the running UID at every boot**,
so the daemon and the interactive terminal share the same writable state.

---

## 3. Set the service variables

Service → **Variables** → add all five:

```
CLAUDE_CODE_OAUTH_TOKEN = <your token>
GH_TOKEN                = <bot PAT>
KOAN_TELEGRAM_TOKEN     = <bot token>
KOAN_TELEGRAM_CHAT_ID   = <your chat id>
KOAN_DEPLOY             = railway
```

When all five are present, the container **self-provisions non-interactively** —
no shell steps, no onboarding wizard. On boot the entrypoint:

- normalizes volume ownership (so `/app/instance` is writable),
- regenerates `/app/.env` as a **mirror** of these service variables (no symlinks;
  the Railway variables are the source of truth — any extra keys you add to an
  on-disk `.env` are preserved),
- resolves `projects.yaml` and `workspace/` from `/app/instance` first,
- auto-registers each `instance/workspace/<dir>` clone as a project,
- configures **token-only Git** (all `git`/`gh` over HTTPS with `GH_TOKEN`, no SSH key).

---

## 4. Deploy & verify it's alive

1. Click **Deploy** (or push to the branch).
2. Watch **Deployments → Logs**. You want to see the entrypoint banner, the
   binary checks (`✓ claude …`, `✓ gh …`), and the daemon starting.
3. Within ~1 minute you should get a **Telegram message** from your bot (startup
   notification). That's your end-to-end proof: Claude auth + GitHub + Telegram
   are all wired.

Quick chat test: send your bot a message like **`hello`** — it should reply.
Then send **`/status`** — it reports the loop state.

---

## 5. Operate from the Railway terminal

Open Service → **⋯ → Terminal** (or `railway run` / `railway ssh` from the CLI),
then:

```bash
make koan      # attaches to the RUNNING daemon (does NOT restart onboarding)
make status    # process status (run / awake)
make logs      # live tail of the agent loop + bridge
```

On a hosted deploy with a live daemon, `make koan` **observes** the instance
instead of re-onboarding. It only runs the wizard if the volume is genuinely
empty — and if the volume isn't writable, it surfaces a clear permission error
instead of looping.

---

## 6. Add a project to work on

Kōan needs at least one project (a git repo) to act on. Two ways:

**A — clone into the workspace (persists on the volume):**
```bash
cd /app/instance/workspace
git clone https://github.com/<you>/<repo>.git
```
The clone is auto-registered as a project keyed by its directory name on the next
loop tick. (Git auth uses `GH_TOKEN` — no prompt.)

**B — declare it in `instance/projects.yaml`** (also on the volume), then redeploy
or `make restart`. Put project config in **`instance/projects.yaml`**, never in the
repo root — only the volume survives.

Trigger work from Telegram, e.g. `/plan https://github.com/<you>/<repo>/issues/1`.

---

## 7. Re-deploys

Push a commit (or hit Redeploy). After the rebuild:

- `instance/projects.yaml`, `instance/workspace/` clones, and the regenerated
  `/app/.env` all resolve again,
- the onboarding wizard does **not** reappear (the service variables are the
  persistent source of truth),
- the daemon reconnects and resumes pulling missions.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No Telegram message on boot | A required variable is missing/typo'd; check `KOAN_TELEGRAM_TOKEN` + `KOAN_TELEGRAM_CHAT_ID`. |
| Permission denied on `/app/instance` | Volume not mounted at exactly `/app/instance`, or `KOAN_DEPLOY=railway` not set (it's the chown trigger). |
| Onboarding wizard reappears | A required service variable is missing → container can't self-provision. |
| Git prompts for a username/password | `GH_TOKEN` unset or lacks `repo` scope. |
| Claude "not authenticated" in logs | `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) missing/expired. Run the `auth` entrypoint command to inspect. |
| Projects gone after a redeploy | Config/clones were outside the volume — keep them under `instance/`. |

---

## Notes for local / dev installs

With `KOAN_DEPLOY` **unset**, every Railway-specific helper early-returns: no
chown and no `.env` regeneration. The one globally-active change is that
`instance/projects.yaml` and `instance/workspace/` take precedence when they
exist; installs without those files resolve the repo-root `projects.yaml` /
`workspace/` exactly as before. This file applies only to hosted Railway deploys.
