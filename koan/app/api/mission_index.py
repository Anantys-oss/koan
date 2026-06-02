"""Sidecar index for API-queued missions.

Tracks missions queued via the REST API in instance/.api-missions.json.
The index is separate from missions.md to avoid modifying its format.

Each record:
    {
        "id": "<uuid>",
        "text": "- mission text",
        "project": "name-or-null",
        "status": "pending|in_progress|done|failed|removed",
        "created": <epoch-float>,
        "result_line": "optional last status line"
    }

Status reconciliation uses parse_sections() to compare what was written
with where the entry now lives in missions.md.
"""

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import atomic_write_json

log = logging.getLogger("koan.api")


_INDEX_FILENAME = ".api-missions.json"


def _index_path(instance_dir: Path) -> Path:
    return instance_dir / _INDEX_FILENAME


def _load_index(instance_dir: Path) -> List[dict]:
    path = _index_path(instance_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        log.warning("mission index is not a list, ignoring: %s", path)
    except (json.JSONDecodeError, OSError) as e:
        log.error("failed to load mission index %s: %s", path, e)
    return []


def _save_index(instance_dir: Path, records: List[dict]) -> None:
    atomic_write_json(_index_path(instance_dir), records)


def record_mission(instance_dir: Path, text: str, project: Optional[str]) -> str:
    """Create a new index record and return its id.

    Returns the existing id if a pending record with the same text already
    exists (dedup guard against double-calls from dashboard + REST API).
    """
    needle = text.lstrip("- ").strip()
    records = _load_index(instance_dir)
    for rec in records:
        if rec.get("status") == "pending":
            stored = rec.get("text", "").lstrip("- ").strip()
            if stored == needle and rec.get("project") == project:
                return rec["id"]
    mission_id = str(uuid.uuid4())
    records.append(
        {
            "id": mission_id,
            "text": text,
            "project": project,
            "status": "pending",
            "created": time.time(),
            "result_line": None,
        }
    )
    _save_index(instance_dir, records)
    return mission_id


def get_mission(instance_dir: Path, mission_id: str) -> Optional[dict]:
    for rec in _load_index(instance_dir):
        if rec.get("id") == mission_id:
            return rec
    return None


def list_missions(
    instance_dir: Path,
    status_filter: Optional[str] = None,
    project_filter: Optional[str] = None,
) -> List[dict]:
    records = _load_index(instance_dir)
    if status_filter:
        records = [r for r in records if r.get("status") == status_filter]
    if project_filter:
        records = [r for r in records if r.get("project") == project_filter]
    return records


def reconcile(instance_dir: Path, missions_file: Path, mission_id: str) -> dict:
    """Reconcile a record's status against current missions.md state.

    Returns the updated record. Persistence is written back to the index.

    Status transitions:
        pending       → in_progress (entry moved to In Progress)
        in_progress   → done (entry disappeared — archived after completion)
        pending       → removed (entry disappeared before starting)
        in_progress   → done is inferred from absence; failed is inferred when
                        entry appears in the failed section.
    """
    records = _load_index(instance_dir)
    target = None
    target_idx = None
    for i, rec in enumerate(records):
        if rec.get("id") == mission_id:
            target = rec
            target_idx = i
            break

    if target is None:
        return {}

    # If already in a terminal state, return as-is
    if target.get("status") in ("done", "failed", "removed"):
        return target

    # Parse missions.md to find current location
    try:
        from app.missions import parse_sections
        content = missions_file.read_text() if missions_file.exists() else ""
        sections = parse_sections(content)
    except Exception as e:
        log.error("reconcile error for mission %s: %s", mission_id, e)
        target["reconcile_error"] = str(e)
        return target

    stored_text = target.get("text", "")
    # Strip the "- " prefix for matching
    needle = stored_text.lstrip("- ").strip()

    def _in_section(section_items: List[str]) -> bool:
        for item in section_items:
            if needle in item:
                return True
        return False

    prev_status = target.get("status", "pending")

    if _in_section(sections.get("pending", [])):
        new_status = "pending"
    elif _in_section(sections.get("in_progress", [])):
        new_status = "in_progress"
    elif _in_section(sections.get("done", [])):
        new_status = "done"
        # Extract result_line from the done entry
        for item in sections.get("done", []):
            if needle in item:
                target["result_line"] = item.split("\n")[0][:200]
                break
    elif _in_section(sections.get("failed", [])):
        new_status = "failed"
        for item in sections.get("failed", []):
            if needle in item:
                target["result_line"] = item.split("\n")[0][:200]
                break
    else:
        # Not found in any section
        if prev_status == "in_progress":
            new_status = "done"  # archived after completion
        else:
            new_status = "removed"

    target["status"] = new_status
    records[target_idx] = target
    _save_index(instance_dir, records)
    return target


def cancel_mission(instance_dir: Path, mission_id: str) -> bool:
    """Mark a record as removed (caller must also remove from missions.md)."""
    records = _load_index(instance_dir)
    for i, rec in enumerate(records):
        if rec.get("id") == mission_id:
            records[i]["status"] = "removed"
            _save_index(instance_dir, records)
            return True
    return False


_LIFECYCLE_TS = re.compile(r"\s*[⏳▶✅❌]\s*\([^)]*\)")


def _normalize_for_match(text: str) -> str:
    """Strip leading ``- ``, lifecycle timestamps, and whitespace."""
    text = text.lstrip("- ").strip()
    return _LIFECYCLE_TS.sub("", text).strip()


def cancel_by_text(instance_dir: Path, text: str) -> bool:
    """Mark the first pending record matching text as removed.

    Uses exact match after normalization (strip leading ``- ``,
    lifecycle timestamps, and whitespace) to avoid false positives.
    """
    needle = _normalize_for_match(text)
    records = _load_index(instance_dir)
    for i, rec in enumerate(records):
        if rec.get("status") != "pending":
            continue
        stored = _normalize_for_match(rec.get("text", ""))
        if stored == needle:
            records[i]["status"] = "removed"
            _save_index(instance_dir, records)
            return True
    return False
