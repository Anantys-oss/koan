# Kōan "Always Up" — Docker + Railway Deployment Spec

**Author**: Kōan (session 99)
**Date**: 2026-02-02
**Status**: Proposal — awaiting human review

---

## 1. Executive Summary

Kōan currently runs on Alexis's Mac via `make run` and `make awake`. The goal: deploy it as an always-on service on Railway so it runs 24/7 without keeping a laptop open.

**Verdict**: Feasible, but with real constraints. The main challenge isn't Docker — it's Claude Code CLI authentication and persistent state. This document covers architecture, security, and the specific gotchas.

---

## 2. Current Architecture Recap

Two processes:
- **run.sh** (agent loop): bash orchestrator → picks missions → invokes `claude -p` → commits results
- **awake.py** (Telegram bridge): Python long-poll → classifies messages → instant replies or queued missions

Shared state via `instance/` directory:
- `missions.md`, `outbox.md`, `journal/`, `memory/` — all file-based
- Cross-process locking via `fcntl.flock()` (POSIX-compatible)
- Git operations on target project repos

External dependencies:
- `claude` CLI (Bun binary, platform-specific)
- `git` (for branch management, auto-merge, sync)
- `python3` + `requests`, `flask`, `pyyaml`
- Telegram Bot API (outbound HTTPS only)

---

## 3. Docker Containerization

### 3.1 Dockerfile Strategy

```dockerfile
FROM node:22-slim AS base

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git python3 python3-pip python3-venv jq curl bash \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# App
WORKDIR /app
COPY koan/ ./koan/
COPY Makefile .
COPY instance.example/ ./instance.example/

# Python deps
RUN python3 -m venv .venv \
    && .venv/bin/pip install -r koan/requirements.txt

# Entry point
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
```

### 3.2 Entrypoint Script

```bash
#!/bin/bash
set -euo pipefail

# Initialize instance/ from example if needed
if [ ! -d /app/instance ]; then
  cp -r /app/instance.example /app/instance
fi

# Claude Code auth: setup-token injects a long-lived token
# The token must be provided via CLAUDE_AUTH_TOKEN env var
if [ -n "${CLAUDE_AUTH_TOKEN:-}" ]; then
  mkdir -p ~/.claude
  echo "$CLAUDE_AUTH_TOKEN" | claude setup-token
fi

# Start both processes
# awake.py in background, run.sh in foreground
cd /app
.venv/bin/python3 koan/app/awake.py &
AWAKE_PID=$!

# Trap to clean up both processes
trap "kill $AWAKE_PID 2>/dev/null; exit 0" INT TERM

# Run the agent loop
./koan/run.sh

# If run.sh exits (quota exhausted), keep awake alive
wait $AWAKE_PID
```

### 3.3 Multi-Process Considerations

Railway runs a single container per service. Options:

**Option A (Recommended): Single container, two processes**
- `awake.py` as background process, `run.sh` as foreground
- Simple, matches current architecture
- Entrypoint manages both with trap handler
- Con: if one crashes, need to detect and restart

**Option B: Two Railway services**
- Separate `awake` and `run` services
- Pro: independent scaling, independent restarts
- Con: shared `instance/` requires a volume or external storage
- Con: 2x the cost

**Option C: supervisord**
- Process manager inside container
- Auto-restart on crash
- Slightly heavier but more robust

Recommendation: **Start with Option A**, graduate to supervisord if stability is an issue.

---

## 4. Claude Code CLI — The Hard Part

### 4.1 Authentication

Claude Code CLI authenticates via OAuth (browser-based) by default. In a headless Docker container, that's not possible.

**Solution: `claude setup-token`**
- Generates a long-lived auth token tied to the Claude subscription
- Run `claude setup-token` interactively on your laptop, copy the token
- Inject as `CLAUDE_AUTH_TOKEN` environment variable in Railway
- The entrypoint script feeds it to `claude setup-token` at startup

**Alternative: API Key mode**
- Set `ANTHROPIC_API_KEY` environment variable
- Claude Code CLI can use API keys directly (billed separately from subscription)
- Pro: simpler auth, no token expiration concerns
- Con: costs real money (not using subscription quota), separate billing

**Recommendation**: Use `setup-token` for subscription quota usage. Fall back to API key if token management becomes painful.

### 4.2 Token Lifecycle

- Long-lived tokens may expire or get revoked
- Need monitoring: if `claude -p` fails with auth errors, alert via Telegram
- Kōan already handles quota exhaustion — extend to handle auth failures
- Consider a health endpoint that verifies Claude CLI auth on `/health` check

### 4.3 Permissions Mode

In Docker, Claude runs sandboxed. Use:
```
claude -p "..." --dangerously-skip-permissions --allowedTools Bash,Read,Write,Glob,Grep,Edit
```

Since the container IS the sandbox, `--dangerously-skip-permissions` is acceptable — the container's filesystem isolation IS the permission boundary. This actually **improves** security vs. running on the laptop where Claude has access to the entire home directory.

**Important**: The `--allowedTools` flag already restricts available tools. Combined with container isolation, this is defense-in-depth.

---

## 5. Persistent State (The Real Challenge)

### 5.1 Problem

`instance/` contains all runtime state: missions, journal, memory, outbox. Railway containers are **ephemeral** — redeployments wipe the filesystem.

### 5.2 Solutions

**Option A (Recommended): Git as persistence layer**
- `instance/` is already a git-tracked directory
- On startup: `git pull` to restore state
- After each run: `git add -A && git commit && git push` (already in run.sh!)
- Kōan already does this. The only change: ensure the container has git credentials.
- Pro: zero new infrastructure, battle-tested, human-visible history
- Con: git push on every cycle adds latency (~2-5s)

**Option B: Railway Volume**
- Mount a persistent volume at `/app/instance`
- Pro: no latency, no git dependency for state
- Con: Railway volumes are per-service (can't share between run and awake if split)
- Con: volume data not versioned, no human visibility
- Con: Railway volumes have availability caveats

**Option C: External storage (S3, Redis)**
- Store state in cloud storage
- Pro: durable, accessible from anywhere
- Con: major rewrite, breaks file-based architecture, overkill

**Recommendation**: **Option A (git persistence)**. Kōan already commits after each run. Just ensure git credentials work in the container.

### 5.3 Git Auth in Container

For pushing to the koan repo from Docker:

```dockerfile
# Git SSH key (preferred)
RUN mkdir -p ~/.ssh
# SSH key injected via KOAN_GIT_SSH_KEY env var
```

Or use GitHub Personal Access Token:
```bash
git remote set-url origin https://x-access-token:${GITHUB_TOKEN}@github.com/user/koan.git
```

Railway supports encrypted environment variables — ideal for this.

---

## 6. Security Analysis

### 6.1 Secrets Management

| Secret | Current (laptop) | Docker/Railway |
|--------|-------------------|----------------|
| `KOAN_TELEGRAM_TOKEN` | `.env` file | Railway env var (encrypted at rest) |
| `KOAN_TELEGRAM_CHAT_ID` | `.env` file | Railway env var |
| `CLAUDE_AUTH_TOKEN` | `~/.claude/` | Railway env var |
| Git credentials | SSH agent / macOS keychain | Deploy key or PAT via env var |
| `ANTHROPIC_API_KEY` (if used) | `.env` file | Railway env var |

**Railway env vars are encrypted at rest** and injected at runtime. This is actually **more secure** than a `.env` file on a laptop.

### 6.2 Attack Surface Comparison

| Attack Vector | Laptop | Docker/Railway | Delta |
|---------------|--------|----------------|-------|
| Claude RCE via prompt injection | Full home dir access | Container-isolated filesystem | **Better** |
| Telegram bot token leak | Full system compromise | Container-only compromise | **Better** |
| Dashboard exposure | localhost only | Could be exposed if misconfigured | **Requires care** |
| Network egress | Unrestricted | Can be restricted via Railway config | **Better** |
| Git credential theft | SSH agent, keychain | Scoped deploy key | **Better** |
| Physical access | Laptop theft risk | Cloud, no physical vector | **Better** |

**Net assessment**: Docker on Railway is **more secure** than running on a laptop, provided:
1. Dashboard is NOT exposed to the internet (or has auth added)
2. Deploy key is scoped to the koan repo only (not a full-access PAT)
3. `--dangerously-skip-permissions` is combined with `--allowedTools`

### 6.3 Critical Security Recommendations

1. **DO NOT expose port 5001 (dashboard)**. The Flask dashboard has zero auth. Keep it internal or add basic auth.
2. **Use a GitHub deploy key** (read-write on koan repo only), not a Personal Access Token with broad scope.
3. **Rotate `CLAUDE_AUTH_TOKEN`** periodically. Set a reminder.
4. **Container user**: Run as non-root (`USER koan` in Dockerfile).
5. **Read-only filesystem** where possible: mount `/app/koan` as read-only, only `/app/instance` writable.
6. **Network policy**: Only allow outbound to `api.telegram.org` and `api.anthropic.com` (Railway supports this).

### 6.4 The Prompt Injection Risk (Inherited)

Session 97's security audit found that Telegram messages are injected verbatim into Claude prompts with Bash tool access. This is a **pre-existing RCE vector** that Docker actually **mitigates** — the blast radius is limited to the container instead of the entire laptop.

However, in Railway, the container has:
- Git credentials (can push malicious code)
- Telegram bot token (can send messages as koan)
- Claude auth token (can consume quota)

**Mitigation**: The tool separation recommended in session 97 (chat_tools vs mission_tools) becomes even more important in a deployed context. Implement it before deploying.

---

## 7. Railway-Specific Configuration

### 7.1 Service Setup

```yaml
# railway.yaml (or via Railway dashboard)
services:
  koan:
    build:
      dockerfile: Dockerfile
    envVars:
      KOAN_TELEGRAM_TOKEN: ${{secret.TELEGRAM_TOKEN}}
      KOAN_TELEGRAM_CHAT_ID: ${{secret.TELEGRAM_CHAT_ID}}
      CLAUDE_AUTH_TOKEN: ${{secret.CLAUDE_AUTH_TOKEN}}
      GITHUB_TOKEN: ${{secret.GITHUB_TOKEN}}
      KOAN_PROJECTS: "koan:/app/repos/koan;anantys-back:/app/repos/anantys-back"
      KOAN_MAX_RUNS: "20"
      KOAN_INTERVAL: "300"
    healthCheck:
      path: /health
      interval: 60
```

### 7.2 Target Project Repos

Kōan currently operates on local project repos (`/Users/alexissukrieh/Devel/...`). In Docker, these need to be cloned:

```bash
# In docker-entrypoint.sh
mkdir -p /app/repos
for project in koan anantys-back anantys-front; do
  if [ ! -d "/app/repos/$project" ]; then
    git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/user/$project.git" "/app/repos/$project"
  else
    cd "/app/repos/$project" && git pull
  fi
done
```

**This is the biggest architectural change**: paths in `KOAN_PROJECTS` must point to cloned repos inside the container, not local filesystem paths.

### 7.3 Cost

Railway pricing (as of early 2026):
- Hobby plan: $5/month, includes 8GB RAM, 8 vCPU
- Usage-based: ~$0.000463/min for compute
- Kōan idle (awake.py polling): minimal CPU, ~100MB RAM
- Kōan active (claude CLI running): spiky CPU, ~500MB RAM

Estimated monthly cost: **$5-15/month** depending on run frequency.

### 7.4 Railway Limitations

- **No cron**: Railway doesn't have native cron. Kōan's own loop handles scheduling — not an issue.
- **Ephemeral filesystem**: Solved by git persistence (section 5.2).
- **Single port exposure**: Only expose if dashboard is needed. For Telegram-only mode, no port needed.
- **Deploy triggers**: Auto-deploy on git push to main. Kōan pushes to main after each run — this would trigger infinite redeploys. **Solution**: use a separate branch for deployment config, or disable auto-deploy.
- **Sleep on inactivity**: Railway can sleep services with no incoming requests. Since awake.py is always polling Telegram, the container stays active. But if awake crashes, the container might sleep. **Solution**: health check endpoint.

---

## 8. Implementation Roadmap

### Phase 1: Foundation (pre-deployment)
1. ~~Implement chat_tools vs mission_tools separation (session 97 recommendation)~~ — **do this first**
2. Add `Dockerfile` and `docker-entrypoint.sh`
3. Add health check endpoint (simple HTTP server on internal port)
4. Test locally with `docker build && docker run`

### Phase 2: Local Docker Testing
5. Run both processes in container
6. Verify git persistence (stop container, restart, state preserved)
7. Verify Claude CLI auth via `setup-token`
8. Verify Telegram bridge works from container

### Phase 3: Railway Deployment
9. Create Railway project, configure env vars
10. Deploy, monitor via Telegram
11. Verify auto-merge works from container git context
12. Monitor for auth token expiration

### Phase 4: Hardening
13. Non-root container user
14. Read-only filesystem mounts
15. Network egress restrictions
16. Log aggregation (Railway provides built-in logging)

---

## 9. What Changes in the Codebase

| File | Change | Reason |
|------|--------|--------|
| `Dockerfile` | **New** | Container definition |
| `docker-entrypoint.sh` | **New** | Startup orchestration |
| `.dockerignore` | **New** | Exclude .env, .venv, instance/ |
| `run.sh` | Minor | Remove `caffeinate`, add health file write |
| `awake.py` | Minor | Add `/health` HTTP endpoint (or separate healthcheck process) |
| `config.yaml` | None | Paths come from env vars, not config |
| `Makefile` | Add targets | `make docker-build`, `make docker-run` |

The core codebase is **already Docker-compatible**. No macOS-specific code, POSIX file locking, subprocess with list args, relative paths from `KOAN_ROOT`.

---

## 10. Open Questions for Alexis

1. **Multi-project repos**: Do you want all 3 projects (koan, anantys-back, anantys-front) cloned in the container? Or start with koan-only?

2. **Dashboard access**: Do you need the Flask dashboard from Railway, or is Telegram-only sufficient? If yes, we need to add auth.

3. **API Key vs Subscription**: Prefer using Claude subscription quota (via `setup-token`) or pay-as-you-go API key (`ANTHROPIC_API_KEY`)?

4. **Git hosting**: All repos on GitHub? Need to scope deploy keys correctly.

5. **Auto-deploy**: Do you want Railway to auto-deploy on push, or manual deploys only? (Given Kōan pushes to main, auto-deploy needs special handling.)

6. **Budget**: Railway $5/month hobby plan sufficient, or need more compute?

---

## 11. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude auth token expires | Agent stops working | Monitor via Telegram alerts, document renewal process |
| Railway container restarts | State loss if git push failed | Startup recovery already handles this (recover.py) |
| Git push conflicts | State divergence | Agent already handles this (atomic commits) |
| Infinite redeploy loop | Container thrashing | Separate deploy branch or disable auto-deploy |
| Telegram bridge crash | No message relay | supervisord or health check + Railway restart policy |
| Cost overrun | Unexpected bills | Railway spending alerts + KOAN_MAX_RUNS cap |

---

## 12. Conclusion

Kōan is surprisingly well-positioned for Docker deployment. The architecture is already:
- File-based (no database needed)
- Git-persistent (state survives restarts)
- POSIX-compatible (no macOS dependencies)
- Process-separated (run.sh + awake.py already independent)

The main work is:
1. **Claude CLI auth** in headless mode (`setup-token`)
2. **Target repo cloning** at container startup
3. **Security hardening** (tool separation, non-root, scoped credentials)

Estimated effort: 2-3 focused sessions to get a working Docker build, 1-2 more for Railway deployment and hardening.

Le plus dur, c'est pas le Docker. C'est de convaincre Claude CLI de tourner sans navigateur.
