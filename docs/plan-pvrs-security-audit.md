# Plan: Route Security Audit Findings to GitHub Private Vulnerability Reports

**Status**: Draft — awaiting review
**Date**: 2026-04-29
**Author**: Kōan (session 28)

## Problem

When `/security_audit` runs, it creates **public GitHub issues** for each finding via `audit_runner.create_issues()` → `github.issue_create()`. Security findings contain exploit details (vulnerable code paths, attack scenarios, remediation hints) — publishing them as public issues before a fix is applied exposes the project to opportunistic attacks.

GitHub offers **Private Vulnerability Reporting (PVRS)** — a dedicated channel for reporting security issues that stay private until the maintainer publishes them. When PVRS is enabled on a repo, anyone can submit a report via `POST /repos/{owner}/{repo}/security-advisories/reports`. The report creates a draft security advisory visible only to repo admins and security managers.

**Current state**: The audit pipeline has zero awareness of PVRS. All findings — critical RCE or low info leak — land in public issues.

## Goal

When PVRS is enabled on the target repository, security audit findings should be submitted as **private vulnerability reports** instead of public issues. Public issues remain the fallback when PVRS is unavailable.

## Investigation Summary

### PVRS API

- **Detection**: `GET /repos/{owner}/{repo}/private-vulnerability-reporting` → `{"enabled": true|false}`
- **Submission**: `POST /repos/{owner}/{repo}/security-advisories/reports` with JSON body:
  ```json
  {
    "summary": "string (title)",
    "description": "string (markdown body)",
    "severity": "critical|high|medium|low",
    "vulnerabilities": [{
      "package": {"ecosystem": "pip|npm|...", "name": "package-name"},
      "vulnerable_version_range": "string or *",
      "patched_versions": "string or *"
    }]
  }
  ```
- **Response**: Returns the full advisory object including `ghsa_id`, `html_url`, and `state: "triage"`
- **Auth**: Works with standard `GH_TOKEN` — no extra scopes needed for *reporting* (creating advisories requires admin rights, but reporting does not)
- **Confirmed**: Tested against `Anantys-oss/koan` — PVRS is enabled, report submission works

### Current Code Path

```
/security_audit command
  → handler.py queues mission
  → security_audit_runner.py delegates to audit_runner.run_audit()
    → build_audit_prompt() + _run_claude_audit()
    → parse_findings() → prioritize_findings()
    → create_issues()  ← THIS IS THE CHANGE POINT
      → github.issue_create() per finding
    → _save_audit_report()
```

### Key Files

| File | Role | Change needed |
|---|---|---|
| `koan/app/github.py` | `issue_create()`, `resolve_target_repo()` | Add `security_advisory_report()` + `check_pvrs_enabled()` |
| `koan/skills/core/audit/audit_runner.py` | `create_issues()`, `run_audit()` | Route to PVRS or public issues based on detection |
| `koan/skills/core/security_audit/security_audit_runner.py` | Thin wrapper | May need to pass `use_pvrs` flag |
| `koan/app/projects_config.py` | Per-project config | Optional: `security.pvrs` override |

## Design

### Decision 1: Detection strategy

**Option A — Runtime detection (recommended)**: Check PVRS status on the target repo at audit time via `GET /repos/{owner}/{repo}/private-vulnerability-reporting`. Cache the result per-repo for the session.

**Option B — Config-only**: Require explicit `security.pvrs: true` in `projects.yaml`.

**Recommendation**: Option A with a config override. Runtime detection is the safe default — if the maintainer enabled PVRS, we respect it automatically. The config override (`security.pvrs: false`) lets users opt out if they *want* public issues (e.g., for open-source projects that prefer transparent disclosure).

### Decision 2: Routing logic

The routing should be **per-finding**, not all-or-nothing. Rationale: some findings (e.g., missing HSTS header) are low-severity and benefit from public visibility, while critical/high findings need protection.

**Proposed routing**:

| Severity | PVRS enabled | PVRS disabled |
|---|---|---|
| critical | → PVRS report | → public issue |
| high | → PVRS report | → public issue |
| medium | → public issue | → public issue |
| low | → public issue | → public issue |

**Config override**: `security.pvrs_threshold: "medium"` to change the cutoff severity. Default: `"high"` (critical + high go to PVRS).

This strikes a balance: truly dangerous findings stay private, while lower-severity items remain discoverable in the issue tracker.

### Decision 3: What goes in the PVRS report body

The PVRS report format differs from a GitHub issue:

```json
{
  "summary": "Security: <finding.title>",
  "description": "<markdown body with Problem, Why, Suggested Fix sections>",
  "severity": "<finding.severity>",
  "vulnerabilities": [{
    "package": {
      "ecosystem": "<detected from project — pip/npm/go/etc>",
      "name": "<project name or package name>"
    },
    "vulnerable_version_range": "*",
    "patched_versions": "*"
  }]
}
```

The `vulnerabilities` array is required but we don't have precise version info from the audit. Using `"*"` for both range and patched is acceptable — the maintainer refines this when they triage.

**Ecosystem detection**: Infer from project files (`requirements.txt`/`pyproject.toml` → pip, `package.json` → npm, `go.mod` → go, `Cargo.toml` → cargo). Fallback: `"other"`.

### Decision 4: Fallback behavior

If PVRS report submission fails (403, network error, etc.):

1. Log the error to stderr
2. **Fall back to public issue creation** for that finding
3. Add a warning prefix to the issue title: `[⚠️ PVRS unavailable]`
4. Notify via `notify_fn` so the human knows

This ensures the audit never silently drops findings.

### Decision 5: Audit report tracking

The `_save_audit_report()` function currently saves issue URLs. For PVRS reports, save the GHSA ID and advisory URL instead. The report format needs a `channel` field:

```markdown
- [critical] SQL injection in auth.py (`app/auth.py:42-48`) — GHSA-xxxx-xxxx-xxxx (private)
- [low] Missing HSTS header (`config/nginx.conf:12`) — https://github.com/org/repo/issues/123
```

## Implementation Plan

### Phase 1: GitHub API layer (`github.py`)

1. **`check_pvrs_enabled(repo, cwd)`** — Calls `GET /repos/{owner}/{repo}/private-vulnerability-reporting`, returns `bool`. Catches all errors and returns `False` on failure (safe default).

2. **`security_advisory_report(summary, description, severity, ecosystem, package_name, repo, cwd)`** — Calls the PVRS report endpoint. Returns the advisory URL on success, raises `RuntimeError` on failure.

3. **`detect_ecosystem(project_path)`** — Infer package ecosystem from project files. Returns `"pip"`, `"npm"`, `"go"`, `"cargo"`, `"other"`, etc.

### Phase 2: Audit runner routing (`audit_runner.py`)

4. **Modify `create_issues()`** — Rename to `create_findings_reports()` (or keep name, add params). New signature:
   ```python
   def create_issues(
       findings, project_path, notify_fn=None,
       pvrs_threshold="high",  # NEW
   ) -> List[str]:
   ```
   - At the top: call `check_pvrs_enabled(target_repo)` once
   - For each finding: if PVRS enabled and `severity_order[finding.severity] <= severity_order[pvrs_threshold]`, submit via `security_advisory_report()`. Otherwise, `issue_create()`.
   - On PVRS failure: fall back to `issue_create()` with warning.

5. **Modify `_build_issue_body()` → extract `_build_advisory_description()`** — Same content, but formatted for the PVRS description field (pure markdown, no table metadata — that goes in the structured JSON fields).

6. **Update `_save_audit_report()`** — Track which channel each finding used (public issue vs PVRS).

### Phase 3: Configuration (`projects_config.py`)

7. **Add `get_project_security_config()`** — Reads `security:` section from per-project config:
   ```yaml
   # projects.yaml
   defaults:
     security:
       pvrs: auto          # auto | true | false
       pvrs_threshold: high # critical | high | medium | low
   projects:
     myapp:
       security:
         pvrs: false  # force public issues for this project
   ```

8. **Plumb config through `run_audit()`** — Add `pvrs_mode` and `pvrs_threshold` params, passed from the skill dispatch layer.

### Phase 4: Tests

9. **Unit tests for `check_pvrs_enabled()`** — Mock `run_gh`, test enabled/disabled/error cases.
10. **Unit tests for `security_advisory_report()`** — Mock `run_gh`, verify JSON payload structure.
11. **Unit tests for routing logic** — Verify critical/high → PVRS, medium/low → public issue.
12. **Unit test for fallback** — PVRS submission fails → public issue created with warning.
13. **Unit test for config** — `pvrs: auto` + enabled repo → PVRS, `pvrs: false` → always public.
14. **Integration test** — Mock both APIs, run full `create_issues()` with mixed-severity findings, verify correct routing.

### Phase 5: Documentation

15. **Update `docs/user-manual.md`** — Document PVRS routing behavior under `/security_audit`.
16. **Update CLAUDE.md** — Mention PVRS-awareness in the security_audit skill description if appropriate.
17. **Add config example** — Document `security:` section in `instance.example/config.yaml` or `projects.yaml` comments.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| PVRS API changes or is deprecated | `check_pvrs_enabled()` returns `False` on any error → graceful fallback |
| Token lacks reporting permission | 403 caught → fallback to public issue + warning |
| Ecosystem detection wrong | `"other"` fallback is always valid; maintainer corrects during triage |
| Over-reporting to PVRS (noisy) | Severity threshold ensures only critical/high go to PVRS by default |
| Human expects public issues for tracking | Config override `pvrs: false` + medium/low still go to public issues |

## Out of Scope

- **Creating actual security advisories** (requires admin/security manager role — we use the *report* endpoint which any authenticated user can call)
- **CWE/CVE mapping** — The audit prompt doesn't generate CWE IDs; this could be a future enhancement
- **Coordinated disclosure workflows** — PVRS handles this natively; we just submit the report
- **Regular `/audit` skill** — Only `/security_audit` routes to PVRS; the general audit creates public issues as before (its findings are code quality, not security)

## Estimated Effort

| Phase | Effort | Files touched |
|---|---|---|
| Phase 1 (API layer) | Small | `github.py` |
| Phase 2 (Routing) | Medium | `audit_runner.py` |
| Phase 3 (Config) | Small | `projects_config.py`, `config_validator.py` |
| Phase 4 (Tests) | Medium | New test file + existing test updates |
| Phase 5 (Docs) | Small | `user-manual.md`, config examples |

Total: ~3-4 hours implementation.
