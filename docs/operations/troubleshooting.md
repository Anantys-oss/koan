# Troubleshooting

Common operational issues and how to resolve them.

## Quick Diagnostic

Run `/doctor` first — it catches many common problems and can auto-repair with `/doctor --fix`. Use `/doctor --full` to include connectivity checks (Telegram, GitHub, CLI provider).

## Agent Loop Issues

### Agent not picking up missions

1. **Check if paused** — `/pause` or quota exhaustion pauses the agent. Run `/status` to see the current state. Use `/resume` to unpause.
2. **Check quota** — `/quota` shows remaining budget. If below `stop_at_percent` (default 5%), the agent waits for quota reset. Override with `/quota 50` if the estimate is wrong.
3. **Check focus mode** — `/focus` locks the agent to a specific project. If a mission is queued for a different project, it won't be picked up. Use `/unfocus` to release.
4. **Check passive mode** — `/passive` blocks all execution. Use `/active` to resume.
5. **Check schedule** — if `schedule.active_hours` is configured, the agent only works during those hours.
6. **Check `missions.md`** — missions stuck under a non-canonical section header won't be picked up. Run `/doctor --fix` to reconcile malformed sections.

### Agent stuck on a mission (looping / not making progress)

1. **Stagnation detection** — if enabled, the agent auto-kills sessions stuck in a loop. Check the stagnation config in `config.yaml`: `stagnation.check_interval_seconds`, `abort_after_cycles`, and `max_retry_on_stagnation`.
2. **Manual abort** — `/abort` kills the current mission and marks it Failed. The next mission in the queue picks up.
3. **Check `/live`** — see if the agent is producing output or silently hung.

### "Quota exhausted" when quota is actually available

The internal usage estimate can drift from reality. Use `/quota <N>` to override (e.g., `/quota 50` tells the agent it has 50% remaining). This clears any quota-related pause.

**Repeated false pauses right after `/resume` (especially fixed in a newer version):** if the agent keeps pausing for "quota" on every resume even though quota is fine — and a `git log` shows a relevant fix already landed — the running daemon may be executing **stale code from before the fix**. The run loop is long-lived; `/update` re-execs the interpreter to load new code (see [auto-update](auto-update.md)), but a daemon started on a much older version (or one whose update didn't complete) can keep running the old modules in memory. Do a full restart so a fresh process loads the current code:

```bash
make stop && make start          # run as the account that owns the daemon
```

If `make stop` reports processes "stopped" but `pgrep -fl app/run.py` still shows a `run.py` (and `cat instance/.koan-run.pid` is missing), you have an **orphaned daemon** that escaped PID tracking — likely owned by a different user (e.g. a dedicated bot account). Kill it from that account (`kill -9 <pid>`) before `make start`.

### Agent loop process not running

1. Check `make status` to see which processes are alive.
2. Check log files: `make logs` or `tail -100 instance/logs/run.log`.
3. If the process crashed, check for a stale PID file — `/doctor --fix` removes orphaned PID files.

## Git & Worktree Issues

### Orphaned or stale worktrees

Parallel sessions create worktrees under `.worktrees/`. If a session crashes, worktrees can linger:

```bash
# From the project directory
git worktree list          # See all worktrees
git worktree prune         # Remove stale references
```

Kōan cleans up worktrees on startup during crash recovery. If you see stale worktrees, restarting the agent loop should clear them.

### Branch conflicts (can't checkout, can't push)

1. **Force-push protection** — Kōan uses `koan/*` branches (configurable via `branch_prefix`). If the branch already exists on remote from another instance, the agent may fail to push.
2. **Shared project repos** — if multiple Kōan instances target the same repo, ensure each uses a distinct `branch_prefix` in `config.yaml`.

### SSH authentication failures

See the [SSH Setup Guide](../setup/ssh-setup.md) for detailed scenarios. Quick checks:

```bash
ssh -T git@github.com          # Test basic SSH connectivity
ssh-add -l                      # Are keys loaded in the agent?
make ssh-forward                # Refresh agent socket (systemd deployments)
```

## Memory & Journal Issues

### Memory files growing too large

Memory compaction runs automatically every 24 hours (configurable). To force compaction immediately:

```bash
python3 koan/app/memory_manager.py <instance_dir> compact-learnings [project-name]
```

Configure thresholds in `config.yaml`:
```yaml
memory:
  learnings_max_lines: 100      # Target after semantic compaction
  learnings_hard_cap: 200       # Absolute max (safety net)
  compaction_interval_hours: 24
```

### Missing memory or journal directories

Run `/doctor --fix` — it recreates missing `memory/` and `journal/` directories.

## Bridge (Telegram/Slack) Issues

### Bot not responding to Telegram messages

1. **Check bridge is running** — `make status` shows if the awake/bridge process is alive.
2. **Check Telegram token** — verify `KOAN_TELEGRAM_TOKEN` is set in `.env`.
3. **Check logs** — `/logs awake` or `tail -100 instance/logs/awake.log`.
4. **Polling interval** — the bridge polls Telegram every 3 seconds. Messages should appear within 3s.

### Outbox messages not being delivered

Messages queue in `instance/outbox.md`. The bridge flushes this to Telegram on each poll cycle. If messages are stuck:

1. Check the bridge is running (`make status`).
2. Check for Telegram API rate limiting in `awake.log`.
3. If the outbox has grown large, restart the bridge (`make stop && make start`).

## GitHub Integration Issues

### GitHub @mentions not triggering missions

1. **Bot nickname** — verify `github_nickname` in `config.yaml` matches the bot's GitHub username.
2. **Authorized users** — check `authorized_users` in `projects.yaml` for the project.
3. **Notification polling** — by default polls every 60-300s. Use `/check_notifications` to force an immediate check.
4. **Permissions** — the bot's GitHub token must have read access to the repository.

### GitHub webhook not receiving events

1. Verify the webhook secret matches `KOAN_GITHUB_WEBHOOK_SECRET`.
2. Check the webhook endpoint is reachable from GitHub's servers.
3. See [GitHub Webhooks](../messaging/github-webhooks.md) for setup details.

## CLI Provider Issues

### Claude Code not found or not working

1. Verify the CLI is installed: `which claude` or `claude --version`.
2. Check `KOAN_CLI_PROVIDER` in `.env` (default: `claude`).
3. See [Provider Setup](../providers/) for provider-specific configuration.

### Provider quota / rate limit errors

Kōan detects quota-limit messages from CLI output and auto-pauses. The pause lifts 10 minutes after the reported reset time. If the reset time can't be parsed, Kōan pauses for 5 hours. Use `/quota <N>` to override the estimate and `/resume` to unpause early.

## Parallel Session Issues

### Sessions not running in parallel

1. Check `max_parallel_sessions` in `config.yaml` (default: 2).
2. Only one mission can target the same project at once (per-project serialization).
3. GitHub operations (`gh` CLI) may rate-limit parallel requests.

### Shared deps causing conflicts

If a mission's build step modifies dependencies (e.g., `npm install`), it can affect other sessions sharing the same directory via `shared_deps`. Consider removing the dependency directory from `shared_deps` if this is a recurring issue.

## Configuration Issues

### Config drift after update

Run `/config_check` to detect keys missing from or extra to the template. The same check runs as part of `/doctor`. Review the reported diff and update your `config.yaml` accordingly.

### Config changes not taking effect

Most config is read on each iteration. A few settings (process-level: API bind, ports) require a restart. Use `/restart` if a config change doesn't seem to take effect.

## When Nothing Else Works

1. **Collect diagnostics**: `/doctor --full` output, recent logs (`/logs all`), and `make status`.
2. **Restart the stack**: `make stop && make start`.
3. **Check upstream issues**: review the [GitHub repository](https://github.com/Anantys-oss/koan) for known issues.
4. **Export a snapshot**: `/snapshot` before making destructive changes — it backs up memory state.
