# Deploy Kōan on Railway

Kōan runs as a single hosted container on Railway (or a similar single-container
PaaS) behind one flag: `KOAN_DEPLOY=railway`. The setup is **symlink-free** and
survives every re-deploy.

## Steps

1. **New Service → Deploy from GitHub →** your `koan` fork.
2. Add a **Volume** mounted at `/app/instance`.
3. Set the service variables:
   - `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`)
   - `KOAN_GH_TOKEN` (the bot's GitHub token — see the caveat below)
   - `KOAN_TELEGRAM_TOKEN`
   - `KOAN_TELEGRAM_CHAT_ID`
   - `KOAN_DEPLOY=railway`
4. Deploy.

When all five variables are present the container provisions itself
non-interactively — no shell steps required.

### GitHub token: use `KOAN_GH_TOKEN`, not `GH_TOKEN`

Railway's GitHub integration **injects its own `GH_TOKEN`** at runtime — a
user-to-server token (`ghu_*`) for the operator account that connected the
repo — and it **overwrites any `GH_TOKEN` you set** in the service variables.
Left as-is, Kōan would push, comment, and open PRs as the operator rather than
as its own bot identity.

To keep Kōan on its own identity, set **`KOAN_GH_TOKEN`** (a bot PAT or
fine-grained token) instead. Kōan resolves `KOAN_GH_TOKEN` with priority over
`GH_TOKEN` and exports it as `GH_TOKEN` for all `git`/`gh` operations, so the
platform-injected value is ignored. `GH_TOKEN` alone still works on platforms
that don't hijack it — `KOAN_GH_TOKEN` is only needed where the environment
overwrites `GH_TOKEN`.

## What the flag does

On every boot, `KOAN_DEPLOY=railway` makes the entrypoint:

- **Normalize volume ownership.** PaaS volumes mount as `root:root`, so the
  image boots as root, `chown`s the `/app/instance` volume to the `koan` user,
  then **drops privileges via `gosu`** and re-execs as `koan` — every
  long-running process (agent, bridge, supervisord) stays non-root. This is
  what makes a volume mounted at `/app/instance` writable on the first boot
  and across re-deploys.
- **Regenerate `/app/.env` as a mirror** of the service variables. No symlinks
  and no `.env` on the volume — Railway service variables are the persistent
  source of truth. Operator-added keys in any on-disk `.env` are preserved.
- Rely on Kōan resolving `projects.yaml` and `workspace/` from `instance/`
  first, so project config and clones survive re-deploys (folds in #2074).
  This `instance/`-first resolution is a global default (all installs), not
  gated on `KOAN_DEPLOY` — it is backward compatible because existing installs
  without an `instance/projects.yaml` keep using the repo-root file.
- **Auto-register** every `instance/workspace/<dir>` clone as a project (keyed
  by directory name) via the existing merged registry.
- Configure **token-only Git**: all `git`/`gh` operations authenticate over
  HTTPS with the resolved token (`KOAN_GH_TOKEN` if set, else `GH_TOKEN`) —
  no SSH key.
- **Start the web dashboard** on `0.0.0.0:5000` (supervisord `dashboard`
  program). On Railway the dashboard is the primary UI; on every other deploy
  the program stays idle. The port is overridable via `KOAN_DASHBOARD_PORT`
  (falls back to `PORT`, then `5000`).
- **Refuse to start `ollama serve`.** The hosted profile defaults to the Claude
  provider, and `ollama serve` is the single largest idle RAM consumer in the
  stack. Even if the resolved provider is `ollama`, the launcher refuses to
  start the bundled `ollama serve` unless you explicitly set
  `KOAN_ALLOW_OLLAMA=1`. On every deploy (Railway or not) `ollama serve` is
  also never started when the resolved provider is anything other than
  `ollama`.

## Dashboard passphrase (`KOAN_DASHBOARD_PWD`)

Because the Railway dashboard binds to a public host, it is **gated by a single
shared passphrase**. Set `KOAN_DASHBOARD_PWD` to any secret string; the first
visit shows a login page, and entering the passphrase unlocks a browser session
(cookie-based, HttpOnly, SameSite=Lax). API routes return `401` until
authenticated. The session secret is derived from the passphrase, so sessions
survive re-deploys.

If `KOAN_DASHBOARD_PWD` is **unset on Railway, the dashboard refuses to start**
(it would otherwise be world-open). Set the passphrase to enable it. When
`KOAN_DEPLOY` is not `railway`, the gate is inert and the dashboard behaves as
the local-only tool it has always been.

This gate is enforced on **both** launch paths through a single helper
(`railway.dashboard_allowed()`): the supervisord `dashboard` program
(`docker/dashboard-supervised.sh`) and the config-driven launcher
(`pid_manager.start_all`, triggered by `dashboard.enabled: true` in
`config.yaml`). With the passphrase unset on Railway, only `run` + `awake`
launch, exposing the minimal worker footprint.

`make koan` either **attaches** to the already-running daemon (status/logs/
dashboard), or runs the onboarding **wizard** on an empty volume. Because the
volume is made writable at boot, config edits (`instance/config.yaml`,
`instance/projects.yaml`, …) are saved on the volume and persist across
re-deploys.

## Re-deploys

Config (`instance/projects.yaml`), workspace clones, and the regenerated `.env`
all resolve after a re-deploy; the onboarding wizard does not reappear once the
service variables are set.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Permission denied on `/app/instance` | Volume not mounted at `/app/instance`, or `KOAN_DEPLOY` unset (the bootstrap chowns it as root, then drops to `koan`). |
| Wizard reappears | A required service variable is missing. |
| Git prompts for a username | No token set — set `KOAN_GH_TOKEN` (or `GH_TOKEN`). |
| PRs/commits authored by the operator, not the bot | Railway injected its own `GH_TOKEN`; set `KOAN_GH_TOKEN` to the bot token (it overrides `GH_TOKEN`). |
| No projects after a redeploy | Put config in `instance/projects.yaml`, not the repo root. |
| `ollama serve` not starting (intended) | Hosted profile refuses it to save RAM. Set provider to `ollama` **and** `KOAN_ALLOW_OLLAMA=1` to run it on Railway. |

## Local / dev installs

With `KOAN_DEPLOY` unset, every Railway-specific helper early-returns — no
chown and no `.env` regeneration. The only globally-active change is that
`instance/projects.yaml` and `instance/workspace/` now take precedence when
they exist; installs without those files keep resolving the repo-root
`projects.yaml` and `workspace/` exactly as before.
