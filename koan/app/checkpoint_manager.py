"""Structured mission progress checkpoints for partial-failure recovery.

When a mission starts, a checkpoint file is created under
``instance/journal/checkpoints/<hash>.json``.  During execution the
checkpoint is updated with branch info and progress signals parsed
from stdout (``CHECKPOINT: {...}`` lines) or pending.md content.

On clean completion the checkpoint file is deleted.  On crash,
``recover.py`` reads the checkpoint to inject structured context into
the recovery prompt instead of a bare re-queue.

Checkpoint schema::

    {
        "mission": "original mission text",
        "project": "project_name",
        "branch": "koan.atoomic/...",
        "run_num": 18,
        "started_at": "ISO8601",
        "updated_at": "ISO8601",
        "steps_done": ["explored codebase", "created branch", ...],
        "steps_remaining": ["run tests", ...]
    }

See GitHub issue #1247 for full design context.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import atomic_write


# Regex matching ``CHECKPOINT: { ... }`` lines in Claude output.
# Matches on single lines — JSON payload must be on one line.
_CHECKPOINT_LINE_RE = re.compile(
    r"CHECKPOINT:\s*(\{[^\n]*\})"
)


def _checkpoints_dir(instance_dir: str) -> Path:
    """Return (and lazily create) the checkpoints directory."""
    d = Path(instance_dir) / "journal" / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def mission_hash(mission_text: str) -> str:
    """Deterministic short hash for a mission (first 12 hex chars of SHA-256)."""
    clean = mission_text.strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:12]


def create_checkpoint(
    instance_dir: str,
    mission_text: str,
    project_name: str,
    run_num: int = 0,
) -> Path:
    """Create a fresh checkpoint file when a mission starts.

    Returns the path to the checkpoint file.
    """
    h = mission_hash(mission_text)
    path = _checkpoints_dir(instance_dir) / f"{h}.json"
    now = datetime.now().isoformat(timespec="seconds")
    data = {
        "mission": mission_text.strip(),
        "project": project_name,
        "branch": "",
        "run_num": run_num,
        "started_at": now,
        "updated_at": now,
        "steps_done": [],
        "steps_remaining": [],
    }
    _write_checkpoint(path, data)
    return path


def update_checkpoint(
    instance_dir: str,
    mission_text: str,
    *,
    branch: Optional[str] = None,
    steps_done: Optional[List[str]] = None,
    steps_remaining: Optional[List[str]] = None,
) -> bool:
    """Merge updates into an existing checkpoint file.

    Only non-None fields are updated.  ``steps_done`` entries are appended
    (deduplicated) rather than replaced.

    Returns True if the checkpoint existed and was updated.
    """
    h = mission_hash(mission_text)
    path = _checkpoints_dir(instance_dir) / f"{h}.json"
    data = _read_checkpoint(path)
    if data is None:
        return False

    if branch is not None:
        data["branch"] = branch
    if steps_done is not None:
        existing = set(data.get("steps_done", []))
        merged = list(data.get("steps_done", []))
        for s in steps_done:
            if s not in existing:
                merged.append(s)
                existing.add(s)
        data["steps_done"] = merged
    if steps_remaining is not None:
        data["steps_remaining"] = steps_remaining

    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_checkpoint(path, data)
    return True


def delete_checkpoint(instance_dir: str, mission_text: str) -> bool:
    """Remove the checkpoint file for a completed mission.

    Returns True if a file was deleted.
    """
    h = mission_hash(mission_text)
    path = _checkpoints_dir(instance_dir) / f"{h}.json"
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def read_checkpoint(instance_dir: str, mission_text: str) -> Optional[Dict]:
    """Read an existing checkpoint for a mission.

    Returns the parsed dict or None if not found / corrupt.
    """
    h = mission_hash(mission_text)
    path = _checkpoints_dir(instance_dir) / f"{h}.json"
    return _read_checkpoint(path)


def list_checkpoints(instance_dir: str) -> List[Dict]:
    """List all checkpoint files in the instance directory.

    Returns a list of parsed checkpoint dicts, newest first.
    """
    d = _checkpoints_dir(instance_dir)
    results = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = _read_checkpoint(f)
        if data is not None:
            results.append(data)
    return results


def parse_checkpoint_markers(stdout_text: str) -> List[Dict]:
    """Extract CHECKPOINT: {...} markers from Claude CLI output text.

    Returns a list of parsed JSON objects from each marker found.
    Invalid JSON markers are silently skipped.
    """
    results = []
    for match in _CHECKPOINT_LINE_RE.finditer(stdout_text):
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict):
                results.append(obj)
        except (json.JSONDecodeError, TypeError):
            continue
    return results


def update_from_stdout(instance_dir: str, mission_text: str, stdout_text: str) -> int:
    """Parse CHECKPOINT markers from stdout and merge into the checkpoint file.

    Returns the number of markers successfully merged.
    """
    markers = parse_checkpoint_markers(stdout_text)
    if not markers:
        return 0

    count = 0
    for marker in markers:
        ok = update_checkpoint(
            instance_dir,
            mission_text,
            steps_done=marker.get("steps_done"),
            steps_remaining=marker.get("steps_remaining"),
            branch=marker.get("branch"),
        )
        if ok:
            count += 1
    return count


def update_from_pending(instance_dir: str, mission_text: str) -> bool:
    """Parse pending.md progress lines and merge into checkpoint as steps_done.

    Reads the pending.md file, extracts timestamped progress lines
    (``HH:MM — description``), and stores them as structured steps.

    Returns True if any steps were extracted and merged.
    """
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    try:
        content = pending_path.read_text()
    except (OSError, FileNotFoundError):
        return False

    steps = _extract_steps_from_pending(content)
    if not steps:
        return False

    return update_checkpoint(
        instance_dir, mission_text, steps_done=steps,
    )


def format_recovery_context(checkpoint: Dict) -> str:
    """Format a checkpoint dict into human-readable recovery context.

    This text is prepended to the recovery prompt so the agent knows
    what was accomplished before the crash.
    """
    lines = ["## Recovery Context (from previous interrupted run)"]
    lines.append("")

    if checkpoint.get("branch"):
        lines.append(f"- **Branch**: `{checkpoint['branch']}`")
    if checkpoint.get("started_at"):
        lines.append(f"- **Started**: {checkpoint['started_at']}")
    if checkpoint.get("project"):
        lines.append(f"- **Project**: {checkpoint['project']}")

    steps_done = checkpoint.get("steps_done", [])
    if steps_done:
        lines.append("")
        lines.append("### Steps already completed:")
        lines.extend(f"- {step}" for step in steps_done)

    steps_remaining = checkpoint.get("steps_remaining", [])
    if steps_remaining:
        lines.append("")
        lines.append("### Steps remaining:")
        lines.extend(f"- {step}" for step in steps_remaining)

    lines.append("")
    lines.append(
        "Resume from where the previous run left off. "
        "Do not redo completed steps unless their output is missing."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_steps_from_pending(content: str) -> List[str]:
    """Extract progress step descriptions from pending.md content.

    Looks for lines matching ``HH:MM — description`` after the ``---``
    separator.  Returns just the descriptions (without timestamps).
    """
    # Pattern: HH:MM followed by dash variants and description
    step_re = re.compile(r"^\d{2}:\d{2}\s*[—–-]\s*(.+)$", re.MULTILINE)
    separator_seen = False
    steps = []
    for line in content.splitlines():
        if line.strip() == "---":
            separator_seen = True
            continue
        if not separator_seen:
            continue
        m = step_re.match(line.strip())
        if m:
            steps.append(m.group(1).strip())
    return steps


def _write_checkpoint(path: Path, data: Dict) -> None:
    """Atomically write a checkpoint JSON file using the project's atomic_write."""
    content = json.dumps(data, indent=2) + "\n"
    atomic_write(path, content)


def _read_checkpoint(path: Path) -> Optional[Dict]:
    """Read and parse a checkpoint JSON file. Returns None on any error."""
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        return None
