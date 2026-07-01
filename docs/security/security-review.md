# Security Review

Kōan can automatically scan mission diffs for security-sensitive patterns before auto-merge. This provides a lightweight safety net that catches common dangerous code patterns without requiring an external tool.

## Overview

When enabled, the security review runs as part of the post-mission pipeline, between reflection and auto-merge. It:

1. **Calculates blast radius** — files changed, modules affected, infrastructure/dependency changes
2. **Scans content patterns** — eval/exec, shell injection, hardcoded secrets, unsafe deserialization, XSS, wildcard CORS, and more
3. **Classifies risk** — low / medium / high / critical based on cumulative score
4. **Logs to journal** — all findings are recorded in the project's daily journal
5. **Optionally blocks auto-merge** — when configured in blocking mode with a severity threshold

The review **fails closed**: if it crashes, times out, or otherwise produces no verdict (git failure, misconfigured `project_path`, malformed diff), auto-merge is **blocked** rather than allowed. A crashed safety gate is indistinguishable from a passed one, so the conservative outcome is to block and alert the operator. The block reason and exception class are written to `.security-audit.jsonl` (see [Audit Trail](#audit-trail)) and a Telegram alert is sent so the underlying failure can be diagnosed.

## Configuration

Security review is configured per-project in `projects.yaml`. See `projects.example.yaml` for a full annotated example.

### Basic setup

```yaml
defaults:
  security_review:
    enabled: true              # Scan diffs for dangerous patterns
    blocking: false            # Log findings but don't block auto-merge
    severity_threshold: high   # Threshold for blocking (when blocking: true)
```

### Blocking mode

When `blocking: true`, auto-merge is skipped if the risk level meets or exceeds `severity_threshold`:

```yaml
defaults:
  security_review:
    enabled: true
    blocking: true             # Block auto-merge on risky changes
    severity_threshold: medium # Block on medium, high, or critical risk
```

### Per-project overrides

Override the defaults for specific projects:

```yaml
projects:
  production-api:
    security_review:
      enabled: true
      blocking: true           # Strict: block on risky changes
      severity_threshold: medium

  internal-tool:
    security_review:
      enabled: false           # Skip review for low-risk internal tools
```

### Variant analysis

When variant analysis is enabled, security findings from the diff are used to scan the **entire project** for similar occurrences. This turns a single-diff review into a codebase-wide vulnerability sweep. Semgrep is preferred when available (structured JSON output, language-aware file selection); grep is the fallback.

```yaml
defaults:
  security_review:
    enabled: true
    variant_analysis:
      enabled: true              # Scan codebase for sibling occurrences
      max_variant_missions: 3    # Cap on investigation missions dispatched
```

When variants are found:
- A `[VARIANT]` section is appended to the journal
- Investigation missions tagged `[security-variant]` are dispatched to the pending queue
- Dedup tracker (`.variant-dispatch-tracker.json`) prevents re-dispatching the same location

### Options

| Setting | Default | Description |
|---|---|---|
| `enabled` | `false` | Run the security review on every mission. |
| `blocking` | `false` | Block auto-merge when risk meets the threshold. When false, findings are logged but auto-merge proceeds. |
| `severity_threshold` | `high` | Minimum risk level that triggers a block (when `blocking: true`). One of: `low`, `medium`, `high`, `critical`. |
| `variant_analysis.enabled` | `false` | Scan the full project for sibling occurrences of detected patterns. |
| `variant_analysis.max_variant_missions` | `3` | Maximum number of investigation missions dispatched per review. |

## What It Detects

### Content patterns (added lines only)

The review scans only added lines in the diff (`+` lines), ignoring removed code:

- **`eval()` / `exec()`** — dynamic code execution
- **`subprocess` with `shell=True`** — shell injection risk
- **`os.system()`** — shell command execution
- **SQL string formatting** — potential SQL injection
- **Hardcoded secrets** — `api_key = "..."`, `password = "..."`
- **SSL/TLS verification disabled** — `disable_ssl`, `verify=False`
- **Overly permissive permissions** — `chmod 777`, `chmod 666`
- **Verification bypass** — `--no-verify` flags
- **Wildcard CORS** — `Access-Control-Allow-Origin: *`
- **Unsafe deserialization** — `pickle.load()`, `marshal.load()`
- **XSS vectors** — `.innerHTML =`, `dangerouslySetInnerHTML`

### Blast radius factors

- Number of files changed (>5, >10, >20 files increase risk)
- Sensitive file paths (secrets, credentials, auth, tokens, configs)
- Infrastructure files (Dockerfile, docker-compose, Makefile)
- Dependency files (requirements.txt, package.json, pyproject.toml, etc.)
- Number of top-level modules affected

## Risk Scoring

The risk level is calculated from a cumulative score:

| Risk Level | Score Threshold |
|---|---|
| Low | 0+ |
| Medium | 6+ |
| High | 12+ |
| Critical | 20+ |

Points are awarded for:
- File count: 1 (>5 files), 2 (>10), 4 (>20)
- Each sensitive file: 3 points
- Infrastructure changes: 3 points
- Dependency changes: 2 points
- Multiple modules: 1 (>1 module), 2 (>3 modules)
- Each content finding: 2 points

## Journal Output

Review results are written to the project's daily journal (`instance/journal/YYYY-MM-DD/project.md`):

```markdown
## Security Review — risk: medium (score: 8)
- Files: 7, Sensitive: 1, Modules: 2
- ⚠ Dependency changes detected
- Content findings (2):
  - eval() usage: `result = eval(user_input)`
  - hardcoded secret: `api_key = "sk-live-..."`
- **Auto-merge blocked** by security review
```

## Audit Trail

Every completed review (approved or blocked) and every crashed review appends one JSON line to `instance/.security-audit.jsonl`. This makes the safety gate's behavior auditable after the fact — a crashed review is no longer indistinguishable from a passed one.

```json
{"ts": "2026-06-27T18:40:00", "project": "myapp", "risk_level": "high", "score": 9, "approved": false, "block_reason": "risk=high score=9 >= high", "variant_count": 0, "changed_files": ["src/auth.py"]}
{"ts": "2026-06-27T18:41:10", "project": "myapp", "risk_level": "unknown", "score": 0, "approved": false, "block_reason": "review error", "variant_count": 0, "changed_files": [], "error_class": "CalledProcessError", "error_msg": "git diff failed"}
```

The `error_class` / `error_msg` fields are present only on the exception path, letting operators distinguish "review crashed on a malformed diff" from "review ran and blocked". `GET /v1/metrics` exposes a `security_blocks_7d` count derived from this file (see [REST API](../operations/rest-api.md)).

## Pipeline Integration

The security review runs in the post-mission pipeline in `mission_runner.py`:

1. Verification (quality gate, lint)
2. Reflection
3. **Security review** ← here
4. Auto-merge (skipped if security review blocks)

If the review itself fails (exception or pipeline timeout), it **fails closed**: the error is logged to `.security-audit.jsonl`, auto-merge is blocked, and a Telegram alert is sent. This prevents a crashed safety gate from silently promoting an unreviewed mission to auto-merge. There is intentionally **no config flag** to restore fail-open behavior.

## Variant Analysis

When variant analysis is enabled and the diff contains security-sensitive patterns (e.g., `eval()`, `shell=True`), the system:

1. **Extracts patterns** from content findings into grep-ready regexes
2. **Scans the full project** using semgrep (if installed) or grep
3. **Excludes diff lines** to avoid reporting the already-reviewed code
4. **Logs to journal** with a `[VARIANT]` section listing all hits
5. **Dispatches investigation missions** (capped by `max_variant_missions`) tagged `[security-variant]`
6. **Deduplicates** via a fingerprint tracker (SHA-256 of `project:filepath:lineno`) to avoid re-dispatching

Semgrep is preferred for structured JSON output and language-aware file selection; grep is the always-available fallback. Both use regex matching — semgrep's `pattern-regex` rules do not provide AST-level filtering. Install semgrep for better results, but it is not required.
