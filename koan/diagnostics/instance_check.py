"""
Kōan diagnostic — Instance directory checks.

Validates missions.md structure, outbox.md writability, and
memory directory integrity. Reuses sanity check modules for
missions.md validation.
"""

import os
from pathlib import Path
from typing import List

from diagnostics import CheckResult, FixResult


def run(koan_root: str, instance_dir: str) -> List[CheckResult]:
    """Run instance directory diagnostic checks."""
    results = []
    instance = Path(instance_dir)

    # --- Instance directory exists ---
    if not instance.is_dir():
        results.append(CheckResult(
            name="instance_dir",
            severity="error",
            message=f"Instance directory not found: {instance_dir}",
            hint="Copy instance.example/ to instance/ and configure",
        ))
        return results

    # --- missions.md ---
    missions_path = instance / "missions.md"
    if not missions_path.exists():
        results.append(CheckResult(
            name="missions_md",
            severity="error",
            message="missions.md not found",
            hint=f"Create {missions_path} (see instance.example/missions.md)",
        ))
    else:
        try:
            content = missions_path.read_text()
            from sanity.missions_structure import find_issues
            issues = find_issues(content)
            if issues:
                results.append(CheckResult(
                    name="missions_md",
                    severity="warn",
                    message=f"missions.md has {len(issues)} structural issue(s): {issues[0]}",
                    hint="Run sanity checks or fix missions.md manually",
                    fixable=True,
                ))
            else:
                results.append(CheckResult(
                    name="missions_md",
                    severity="ok",
                    message="missions.md structure is valid",
                ))
        except Exception as e:
            results.append(CheckResult(
                name="missions_md",
                severity="error",
                message=f"missions.md could not be parsed: {e}",
            ))

    # --- Stale in-progress missions ---
    try:
        from app.missions import parse_sections
        if missions_path.exists():
            content = missions_path.read_text()
            sections = parse_sections(content)
            in_progress = sections.get("in_progress", [])
            if in_progress:
                results.append(CheckResult(
                    name="stale_missions",
                    severity="warn",
                    message=f"{len(in_progress)} mission(s) in progress",
                    hint="Check if these are stale from a crash — /cancel or restart to recover",
                    fixable=True,
                ))
            else:
                results.append(CheckResult(
                    name="stale_missions",
                    severity="ok",
                    message="No in-progress missions",
                ))
    except Exception:
        pass  # missions.md issues already reported above

    # --- outbox.md ---
    outbox_path = instance / "outbox.md"
    if not outbox_path.exists():
        # Not an error — outbox.md is created on demand
        results.append(CheckResult(
            name="outbox_md",
            severity="ok",
            message="outbox.md not present (created on demand)",
        ))
    else:
        if os.access(outbox_path, os.W_OK):
            results.append(CheckResult(
                name="outbox_md",
                severity="ok",
                message="outbox.md is writable",
            ))
        else:
            results.append(CheckResult(
                name="outbox_md",
                severity="error",
                message="outbox.md is not writable",
                hint=f"Check file permissions on {outbox_path}",
            ))

    # --- memory/ directory ---
    memory_dir = instance / "memory"
    if not memory_dir.is_dir():
        results.append(CheckResult(
            name="memory_dir",
            severity="warn",
            message="memory/ directory not found",
            hint=f"Create {memory_dir} for agent memory storage",
            fixable=True,
        ))
    else:
        results.append(CheckResult(
            name="memory_dir",
            severity="ok",
            message="memory/ directory exists",
        ))

    # --- journal/ directory ---
    journal_dir = instance / "journal"
    if not journal_dir.is_dir():
        results.append(CheckResult(
            name="journal_dir",
            severity="warn",
            message="journal/ directory not found",
            hint=f"Create {journal_dir} for daily logs",
            fixable=True,
        ))
    else:
        results.append(CheckResult(
            name="journal_dir",
            severity="ok",
            message="journal/ directory exists",
        ))

    return results


def fix(koan_root: str, instance_dir: str) -> List[FixResult]:
    """Auto-repair instance directory issues."""
    results = []
    instance = Path(instance_dir)

    if not instance.is_dir():
        return results

    # --- Fix missions.md structural issues ---
    missions_path = instance / "missions.md"
    if missions_path.exists():
        try:
            from sanity.missions_structure import find_issues, sanitize
            content = missions_path.read_text()
            issues = find_issues(content)
            if issues:
                cleaned, changes = sanitize(content)
                if changes:
                    from app.utils import atomic_write
                    atomic_write(missions_path, cleaned)
                    results.append(FixResult(
                        name="missions_md",
                        success=True,
                        message=f"Fixed {len(changes)} structural issue(s) in missions.md",
                    ))
        except Exception as e:
            results.append(FixResult(
                name="missions_md",
                success=False,
                message=f"Failed to fix missions.md: {e}",
            ))

    # --- Recover stale in-progress missions ---
    if missions_path.exists():
        try:
            from app.recover import recover_missions
            recovered_count, escalated = recover_missions(instance_dir)
            total = recovered_count + len(escalated)
            if total:
                parts = []
                if recovered_count:
                    parts.append(f"{recovered_count} moved to Pending")
                if escalated:
                    parts.append(f"{len(escalated)} escalated to Failed")
                results.append(FixResult(
                    name="stale_missions",
                    success=True,
                    message=f"Recovered stale missions: {', '.join(parts)}",
                ))
        except Exception as e:
            results.append(FixResult(
                name="stale_missions",
                success=False,
                message=f"Failed to recover stale missions: {e}",
            ))

    # --- Create missing directories ---
    for dir_name in ("memory", "journal"):
        dir_path = instance / dir_name
        if not dir_path.is_dir():
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                results.append(FixResult(
                    name=f"{dir_name}_dir",
                    success=True,
                    message=f"Created missing {dir_name}/ directory",
                ))
            except OSError as e:
                results.append(FixResult(
                    name=f"{dir_name}_dir",
                    success=False,
                    message=f"Failed to create {dir_name}/: {e}",
                ))

    return results
