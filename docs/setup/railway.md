# Deploy Kōan on Railway

Kōan runs as a single hosted container on Railway (or a similar single-container
PaaS) behind one flag: `KOAN_DEPLOY=railway`. The setup is **symlink-free** and
survives every re-deploy.

## Steps

1. **New Service → Deploy from GitHub →** your `koan` fork.
2. Add a **Volume** mounted at `/app/instance`.
3. Set the service variables:
   - `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`)
   - `GH_TOKEN`
   - `KOAN_TELEGRAM_TOKEN`
   - `KOAN_TELEGRAM_CHAT_ID`
   - `KOAN_DEPLOY=railway`
4. Deploy.

When all five variables are present the container provisions itself
non-interactively — no shell steps required.

## What the flag does

On every boot, `KOAN_DEPLOY=railway` makes the entrypoint:

- **Normalize volume ownership** to the running UID, so the instance volume is
  always writable.
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
  HTTPS with `GH_TOKEN` — no SSH key.

`make koan` either **attaches** to the already-running daemon (status/logs/
dashboard), or runs the onboarding **wizard** on an empty volume — surfacing a
clear permission error if the volume is not writable.

## Re-deploys

Config (`instance/projects.yaml`), workspace clones, and the regenerated `.env`
all resolve after a re-deploy; the onboarding wizard does not reappear once the
service variables are set.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Permission denied on `/app/instance` | Volume not mounted at `/app/instance`, or `KOAN_DEPLOY` unset (the bootstrap chowns it). |
| Wizard reappears | A required service variable is missing. |
| Git prompts for a username | `GH_TOKEN` unset. |
| No projects after a redeploy | Put config in `instance/projects.yaml`, not the repo root. |

## Local / dev installs

With `KOAN_DEPLOY` unset, every Railway-specific helper early-returns — no
chown and no `.env` regeneration. The only globally-active change is that
`instance/projects.yaml` and `instance/workspace/` now take precedence when
they exist; installs without those files keep resolving the repo-root
`projects.yaml` and `workspace/` exactly as before.
