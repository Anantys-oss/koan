"""Create or update .claude/settings.json with permission allowlists for Kōan.

Kōan operates on its own ``instance/`` directory as runtime state — mission
queue, outbox, journals, memory.  Without a permission allowlist, Claude Code
prompts for approval on every Write/Edit/Bash operation touching those files,
which defeats autonomous operation.

This module writes ``.claude/settings.json`` at the project root with the
minimum allowlist required for autonomous Kōan sessions.

Safe to run multiple times (idempotent): existing keys outside the ``permissions``
block are preserved; the ``permissions`` block is replaced wholesale.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List


# Minimum permission rules for autonomous Kōan sessions.
#
# Write/Edit cover the native file tools; Bash patterns cover shell operations
# the agent uses to update journals and run git/gh workflows.
KOAN_ALLOWLIST: List[str] = [
    # --- instance/ runtime state (missions, outbox, journal, memory) ---
    "Write(instance/**)",
    "Edit(instance/**)",
    # echo-append pattern used for journal updates (relative paths)
    "Bash(echo * >> instance/**)",
    "Bash(echo * >> */instance/**)",
    # cleanup of pending.md at session end
    "Bash(rm instance/**)",
    "Bash(rm -f instance/**)",
    # journal directory creation
    "Bash(mkdir -p instance/**)",
    # --- version control (required for code missions) ---
    "Bash(git status*)",
    "Bash(git log*)",
    "Bash(git diff*)",
    "Bash(git add *)",
    "Bash(git commit*)",
    "Bash(git push*)",
    "Bash(git checkout*)",
    "Bash(git branch*)",
    "Bash(git stash*)",
    "Bash(git fetch*)",
    # --- GitHub CLI (PR creation, issue management) ---
    "Bash(gh pr*)",
    "Bash(gh issue*)",
    "Bash(gh api*)",
    "Bash(gh repo*)",
]


def _settings_path(project_root: Path) -> Path:
    return project_root / ".claude" / "settings.json"


def install(project_root: Path | str | None = None, dry_run: bool = False) -> dict:
    """Write .claude/settings.json with the Kōan permission allowlist.

    Returns a dict with keys:
        path     – absolute path to settings.json
        created  – True if the file was created, False if it already existed
        updated  – True if permissions were changed
        dry_run  – True when no files were written
    """
    root = Path(project_root) if project_root else Path.cwd()
    path = _settings_path(root)

    existing: dict = {}
    created = not path.exists()
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    old_allow = (existing.get("permissions") or {}).get("allow", [])
    new_allow = KOAN_ALLOWLIST

    updated = sorted(old_allow) != sorted(new_allow)

    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing.setdefault("permissions", {})["allow"] = new_allow
        path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return {
        "path": str(path),
        "created": created,
        "updated": updated,
        "dry_run": dry_run,
    }


def main() -> None:
    """CLI entry point: ``python -m app.setup_claude_settings [--dry-run]``."""
    dry_run = "--dry-run" in sys.argv

    import os
    project_root = os.environ.get("KOAN_ROOT", str(Path.cwd()))
    result = install(project_root=project_root, dry_run=dry_run)

    path = result["path"]
    if result["dry_run"]:
        print(f"[dry-run] would write {path}")
    elif result["created"]:
        print(f"→ Created {path}")
    elif result["updated"]:
        print(f"→ Updated {path} (permissions changed)")
    else:
        print(f"✓ {path} already up-to-date")


if __name__ == "__main__":
    main()
