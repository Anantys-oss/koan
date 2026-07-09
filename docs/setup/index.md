# Setup

* [Docker Setup](docker.md) - Covers Docker Compose setup for Koan (pull vs. build from source), workspace project mounts, authentication (Claude/GitHub), volume layout, and troubleshooting common container issues.
* [Environment-variable-only deployment](env-var-deployment.md) - Explains how Koan can run purely from injected environment variables (Railway/Docker/Kubernetes/systemd) without a hand-authored `.env` file, and the precedence rules between env vars and the synthesized `.env`.
* [Running as a launchd Service (macOS)](launchd.md) - Documents running Koan as a macOS launchd user service for auto-restart and login-time startup, including setup, logs, SSH agent forwarding, and troubleshooting.
* [Deploy Kōan on Railway](railway.md) - Details deploying Koan as a single hosted container on Railway via `KOAN_DEPLOY=railway`, covering required service variables, the GitHub token bot-identity caveat, dashboard passphrase gating, and re-deploy behavior.
* [Git SSH Authentication](ssh-setup.md) - Walks through SSH authentication setup for Koan's git operations across macOS direct-run, Linux systemd, and Docker deployment modes, including fallback key generation.
* [Running as a systemd --user Service (Linux, no root)](systemd-user.md) - Describes running Koan as a per-user (rootless) systemd service on Linux, covering unit installation, linger for boot persistence, and PATH preservation for CLI providers.
