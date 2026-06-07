"""
Kōan diagnostic — Claude Code permission allowlist check.

Verifies that .claude/settings.json exists with the instance/** allowlist
entries required for autonomous operation without permission prompts.
"""

import json
from pathlib import Path
from typing import List

from diagnostics import CheckResult, FixResult


# Minimum allowlist entries that must be present.  Extra entries are fine.
_REQUIRED_RULES = {
    "Write(instance/**)",
    "Edit(instance/**)",
}


def _settings_path(koan_root: str) -> Path:
    return Path(koan_root) / ".claude" / "settings.json"


def _read_allow(path: Path) -> List[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("permissions") or {}).get("allow", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def run(koan_root: str, instance_dir: str) -> List[CheckResult]:
    results = []
    path = _settings_path(koan_root)

    if not path.exists():
        results.append(CheckResult(
            name="claude_settings",
            severity="warn",
            message=".claude/settings.json missing — agents will see permission prompts for instance/ writes",
            hint="Run `make permissions` (or `make setup`) to create the allowlist",
            fixable=True,
        ))
        return results

    allow = set(_read_allow(path))
    missing = _REQUIRED_RULES - allow
    if missing:
        results.append(CheckResult(
            name="claude_settings",
            severity="warn",
            message=f".claude/settings.json exists but is missing {len(missing)} required rule(s): {', '.join(sorted(missing))}",
            hint="Run `make permissions` to refresh the allowlist",
            fixable=True,
        ))
    else:
        results.append(CheckResult(
            name="claude_settings",
            severity="ok",
            message=f".claude/settings.json present with {len(allow)} permission rule(s)",
        ))

    return results


def fix(koan_root: str, instance_dir: str) -> List[FixResult]:
    """Auto-create or repair .claude/settings.json."""
    path = _settings_path(koan_root)
    allow = set(_read_allow(path)) if path.exists() else set()
    if _REQUIRED_RULES.issubset(allow):
        return []  # already fine — nothing to do

    try:
        from app.setup_claude_settings import install
        result = install(project_root=koan_root)
        verb = "Created" if result["created"] else "Updated"
        return [FixResult(
            name="claude_settings",
            success=True,
            message=f"{verb} .claude/settings.json with instance/** permission allowlist",
        )]
    except Exception as e:
        return [FixResult(
            name="claude_settings",
            success=False,
            message=f"Could not create .claude/settings.json: {e}. Run `make permissions` manually.",
        )]
