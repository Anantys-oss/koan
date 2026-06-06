# Claude Code Permissions & Kōan Runtime Files

## Overview

Kōan manages its own runtime state (`instance/missions.md`, `instance/recurring.json`, `instance/soul.md`, etc.) via Python atomic writes in the bridge (`awake.py`) and agent loop (`run.py`). **These operations do NOT go through Claude Code's permission system** — they are safe-by-design and require no allowlist configuration.

When the chat system detects an actionable intent (e.g., "restart my recurring tasks"), it routes the command through existing Python handlers, not through Claude's file-write tools. This keeps the chat tool list read-only (Read, Glob, Grep) to prevent Telegram prompt-injection attacks, while still enabling state-changing operations via confirmed commands.

## When Permissions Don't Apply

- **Bridge and agent loop**: Kōan's own Python code reads and writes `instance/*` files directly. No Claude Code calls involved.
- **Chat system**: Intent routing and confirmation happen in Python; dispatch goes through validated handlers.
- **Mission execution**: Missions run via the agent loop, not chat; only the agent loop needs Claude access, and it already has unrestricted permission to your projects' source code.

## When Permissions Do Apply

**Only when a mission is authored to edit the Kōan codebase itself** (e.g., "add a new core skill", "fix a bug in awake.py").

If a mission targets a workspace project other than Kōan, or if it reads Kōan code, permissions are not involved. Permissions only matter when a mission task would run Claude Code with `Edit` or `Write` against Kōan's `instance/` or `koan/` source directories.

## Optional: Allowlist Configuration

### For missions that edit the Kōan repo itself

If you want to author missions that improve Kōan's own code (fixing bugs in the bridge, adding skills, etc.), you have two options:

#### Option 1: Skip permissions globally (simple)

Set `skip_permissions: true` in `instance/config.yaml`:

```yaml
skip_permissions: true
```

This adds `--dangerously-skip-permissions` to the Claude CLI invocation (see `get_skip_permissions()` in `config.py`), so the agent loop runs Claude without the permission dialog. Useful when you trust all mission content.

**Default:** `skip_permissions: false` (disabling permissions is not recommended for security; prefer the allowlist in Option 2).

#### Option 2: Allowlist `instance/` files (recommended, permanent)

Add a rule to `.claude/settings.json` in your Kōan directory to allow Claude to write `instance/` files:

```json
{
  "permissions": {
    "write": [
      "instance/**"
    ]
  }
}
```

This allows Claude to edit all runtime files in `instance/` (missions, recurring, memory, etc.) without a permission prompt.

**Note:** This only applies to the Kōan repo itself. Individual workspace projects have their own `.claude/settings.json` and are unaffected.

### Operator-specific: Don't commit allowlist to the public repo

If you maintain a custom Kōan fork and want to add the allowlist, use `.claude/settings.local.json` (git-ignored) instead of `.claude/settings.json` so the allowlist doesn't leak to the public repository:

```bash
cat >> .claude/settings.local.json <<'EOF'
{
  "permissions": {
    "write": ["instance/**"]
  }
}
EOF
```

## FAQ

**Q: Why does chat not have Write access?**  
A: Chat runs via Telegram, which is an untrusted channel. An attacker could send a prompt-injection payload and trick Claude into editing your mission list. By keeping chat read-only and routing all actions through Python validation, we prevent this attack. Confirmed commands still execute via Python handlers, bypassing the need for Write tools.

**Q: Do I need a permission allowlist to run the agent loop?**  
A: No. The agent loop is Python code in `koan/app/run.py`, not a Claude Code call. It uses atomic writes to manage `instance/` files directly.

**Q: Why does `instance/missions.md` never prompt for permissions?**  
A: Because Kōan's Python code writes it directly via `app.utils.atomic_write()` in `missions.py`, `start_mission()`, etc. This happens in the bridge/agent, not in a Claude Code session.

**Q: Can I write my own missions against the Kōan repo?**  
A: Yes, if you configure the allowlist or use `[skip_permissions]`. This is the intended flow for operators who want Kōan to improve itself — add a mission like "implement [feature]" with the tag, and Kōan will author the code.

## See Also

- [Prompt Guard & Injection Mitigation](../security/prompt-guard.md)
- [Agent Loop & Mission Execution](../architecture/daemon.md)
- [Bridge Architecture](../architecture/overview.md)
