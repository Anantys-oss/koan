---
type: doc
title: "RTK integration"
description: "Explains the optional rtk CLI-proxy integration (detection, awareness injection, hook setup) that compresses dev-command output for token savings."
tags: [operations]
created: 2026-05-28
updated: 2026-05-28
---

# RTK integration

Kōan can optionally lean on [`rtk`](https://github.com/rtk-ai/rtk) — a Rust CLI proxy that compresses common dev-command output (`git`, `ls`, `cat`, `grep`, `pytest`, `cargo`, `gh`, `docker`, …) by 60–90 % before it reaches Claude. Strictly complementary to the [caveman optimisation](../../instance.example/config.yaml): caveman trims what Claude **writes**; rtk trims what Claude **reads**.

`rtk` is **never** a Kōan dependency. If it isn't installed, nothing changes.

## How it plugs in

Three layers, each independently useful:

| Layer | What it does | Activation |
|---|---|---|
| **L1 — Detection** | At boot, log whether `rtk` and `jq` are present and whether the `~/.claude/settings.json` PreToolUse hook is wired up. | Always on (read-only probe). |
| **L2 — Awareness** | Inject `koan/system-prompts/rtk-awareness.md` into Claude's system prompt so Claude prefers `rtk git status` over `git status`. | Default `auto` — on iff the binary is detected. |
| **L3 — Hook setup** | The `/rtk setup` Telegram skill runs `rtk init -g --auto-patch` to install the official PreToolUse hook (transparent rewrite of every Bash command). | Manual — never automatic. |

## Quick start

```bash
# 1. Install rtk on the host (one-time)
brew install rtk
# or: curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh

# 2. Restart Kōan — boot log should show:
#    [init] rtk 0.28.2 detected, hook: inactive

# 3. (optional) From Telegram, install the auto-rewrite hook:
/rtk setup           # preview
/rtk setup confirm   # actually run rtk init -g --auto-patch
```

After step 3, every Bash command Claude runs inside a Kōan mission gets transparently rewritten to its `rtk` equivalent. Nothing changes in Kōan's argv or prompt assembly — the hook fires inside Claude Code itself.

## The `/rtk` skill

| Command | Effect |
|---|---|
| `/rtk` | Show detection status (binary, version, hook, jq, project gate) |
| `/rtk setup` | Preview what `rtk init -g --auto-patch` would change |
| `/rtk setup confirm` | Actually install the PreToolUse hook |
| `/rtk uninstall` | Run `rtk init -g --uninstall` |
| `/rtk gain [args]` | Forward to `rtk gain` (analytics — token savings, history, daily) |
| `/rtk discover [args]` | Forward to `rtk discover` (find missed savings opportunities) |
| `/rtk on` / `/rtk off` | Runtime override — toggles awareness without editing `config.yaml`. Writes `instance/.koan-rtk-override`. |

## Configuration

```yaml
# instance/config.yaml
optimizations:
  rtk:
    enabled: auto         # auto | true | false
                          #   auto = on iff `rtk` is on PATH (default)
    awareness: true       # inject the awareness section into system prompts
    require_jq: true      # warn at boot if jq is missing
```

```yaml
# projects.yaml — per-project opt-out
projects:
  myproject:
    rtk: false            # never inject awareness for this project
```

Resolution order for `is_rtk_mode()`:

1. `instance/.koan-rtk-override` (`/rtk on` / `/rtk off`) — highest priority.
2. `optimizations.rtk.enabled` in `config.yaml`.
3. `auto` → fall through to `app.rtk_detector.detect_rtk()`.

Per-project resolution (`get_project_rtk_enabled`):
- `projects.<name>.rtk: true` or `false` → hard override for that project.
- Anything else (or omitted) → defer to global `is_rtk_mode()`.

## What rtk filters and what it doesn't

The hook only intercepts the **Bash tool** — Claude Code's native `Read` / `Glob` / `Grep` bypass it. The awareness section nudges Claude to prefer `rtk read <file>` and `rtk grep <pat>` for large files, but agents may still default to native tools, capping practical savings below the headline 80 %.

Filters exist for:

- Git: `git status`, `git log`, `git diff`, `git add`, `git commit`, `git push`, `git pull`
- Files: `ls`, `cat`/`read`, `find`, `grep`, `diff`
- GitHub: `gh pr list/view`, `gh issue list`, `gh run list`
- Tests: `pytest`, `jest`, `vitest`, `cargo test`, `go test`, `rspec`, `playwright test`, generic `rtk test <cmd>`
- Build/lint: `tsc`, `ruff check`, `cargo build/clippy`, `golangci-lint`, `eslint/biome`, `rubocop`
- Containers: `docker ps`, `docker logs`, `kubectl pods/logs`
- Cloud: `aws sts/ec2/lambda/logs/cloudformation/dynamodb/iam/s3`
- Misc: `log`, `json`, `curl`, `env`

Unknown commands pass through unchanged — rtk is never destructive.

## Caveats

- **Never auto-patches `~/.claude/settings.json`.** Hook installation only happens via explicit `/rtk setup confirm`.
- **`jq` is required for the hook script.** The detector probes for it independently of `rtk`. If missing, `/rtk` warns but the awareness section still works (Claude calls `rtk` directly via Bash).
- **Telemetry is opt-in.** rtk has its own anonymous usage telemetry, off by default. Kōan never enables it on the user's behalf.
- **Copilot provider is out of scope (v1).** rtk's Copilot support is `deny-with-suggestion` rather than transparent rewrite — friction outweighs savings. Skip the Copilot path for now.
- **Windows native is degraded.** rtk's hook is Unix-only; the awareness section still works.

## Verifying

```bash
# Without rtk on PATH:
python -c "from app.rtk_detector import detect_rtk; print(detect_rtk())"
# RtkStatus(installed=False, ...)

# With rtk installed:
rtk --version           # rtk 0.28.2
KOAN_ROOT=/path .venv/bin/pytest koan/tests/test_rtk_detector.py koan/tests/test_rtk_skill.py -v
```

## Related

- Issue [#1295](https://github.com/Anantys-oss/koan/issues/1295) — the integration plan.
- Issue [#1279](https://github.com/Anantys-oss/koan/issues/1279) — caveman mode (composes orthogonally).
- Modules: `koan/app/rtk_detector.py`, `koan/app/prompt_builder.py` (`_get_rtk_section`), `koan/skills/core/rtk/`.
