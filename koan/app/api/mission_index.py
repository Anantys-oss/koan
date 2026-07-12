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
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import atomic_write_json

log = logging.getLogger("koan.api")


_INDEX_FILENAME = ".api-missions.json"

DEFAULT_RESULT_CAP_BYTES = 256 * 1024  # inline cap; larger spills to a side file
_RESULTS_DIRNAME = ".api-results"


def _index_path(instance_dir: Path) -> Path:
    return instance_dir / _INDEX_FILENAME


def _results_dir(instance_dir: Path) -> Path:
    return instance_dir / _RESULTS_DIRNAME


def _with_result_defaults(rec: dict) -> dict:
    rec.setdefault("result", None)
    rec.setdefault("result_ref", None)
    rec.setdefault("outcome", None)
    return rec


def _authoritative_outcome(instance_dir: Path, text: str) -> Optional[dict]:
    """Latest terminal outcome from the durable OutcomeStore, or None.

    Narrowly catches only the recoverable DB error: a locked/corrupt outcome
    log degrades to the section-scan fallback, but a programming error (bad
    import, wrong signature) must surface rather than silently reverting the
    #2285 fix to the absence-inference heuristic.
    """
    try:
        from app.mission_store.aux_stores import OutcomeStore
        latest = OutcomeStore(str(instance_dir)).latest(text)
    except sqlite3.DatabaseError as e:
        log.error("outcome lookup failed (DB error): %s", e)
        return None
    if not latest:
        return None
    return {
        "status": latest["status"],
        "reason_category": latest.get("reason_category"),
        "detail": latest.get("detail"),
    }


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
            "result": None,
            "result_ref": None,
        }
    )
    _save_index(instance_dir, records)
    return mission_id


def get_mission(instance_dir: Path, mission_id: str) -> Optional[dict]:
    for rec in _load_index(instance_dir):
        if rec.get("id") == mission_id:
            return _with_result_defaults(rec)
    return None


def attach_result(
    instance_dir: Path,
    mission_id: str,
    result: dict,
    *,
    cap_bytes: Optional[int] = None,
    always_inline: Optional[List[str]] = None,
) -> bool:
    """Attach a typed structured result to a mission record.

    Stores the blob inline when its JSON serialization is <= cap_bytes.
    Otherwise spills the full blob to instance/.api-results/<id>.json, sets a
    relative ``result_ref``, and keeps a trimmed inline copy of ``always_inline``
    keys (plus ``result_truncated=True``) so small, always-useful fields (e.g.
    the review verdict/summary) remain readable without an HTTP round-trip.

    No-op (returns False) if the record is missing or already has a non-null
    result/result_ref (idempotent — runs once, on the terminal transition).
    """
    if cap_bytes is None:
        cap_bytes = DEFAULT_RESULT_CAP_BYTES
    records = _load_index(instance_dir)
    for i, rec in enumerate(records):
        if rec.get("id") != mission_id:
            continue
        if rec.get("result") is not None or rec.get("result_ref"):
            return False
        try:
            encoded = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            log.error("attach_result: unserializable result for %s: %s", mission_id, e)
            return False
        if len(encoded.encode("utf-8")) <= cap_bytes:
            rec["result"] = result
            rec["result_ref"] = None
        else:
            _results_dir(instance_dir).mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                _results_dir(instance_dir) / f"{mission_id}.json", result, indent=2
            )
            rec["result_ref"] = f"{_RESULTS_DIRNAME}/{mission_id}.json"
            keep = [k for k in (always_inline or []) if k in result]
            if keep:
                trimmed = {k: result[k] for k in keep}
                trimmed["result_truncated"] = True
                rec["result"] = trimmed
            else:
                rec["result"] = None
        records[i] = rec
        _save_index(instance_dir, records)
        return True
    return False


def load_full_result(instance_dir: Path, mission_id: str) -> Optional[dict]:
    """Return the complete structured result (inline or spilled), or None."""
    rec = get_mission(instance_dir, mission_id)
    if rec is None:
        return None
    ref = rec.get("result_ref")
    if ref:
        try:
            return json.loads((instance_dir / ref).read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.error("load_full_result: cannot read spill for %s: %s", mission_id, e)
            return None
    return rec.get("result")


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
    return [_with_result_defaults(r) for r in records]


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
        return _with_result_defaults(target)

    # Parse missions.md to find current location
    try:
        from app.mission_store.transition import read_sections
        sections = read_sections(missions_file.parent)
    except Exception as e:
        log.error("reconcile error for mission %s: %s", mission_id, e)
        target["reconcile_error"] = str(e)
        return target

    stored_text = target.get("text", "")
    needle = _normalize_for_match(stored_text)

    def _in_section(section_items: List[str]) -> bool:
        for item in section_items:
            if _normalize_for_match(item) == needle:
                return True
        return False

    prev_status = target.get("status", "pending")

    if _in_section(sections.get("pending", [])):
        new_status = "pending"
    elif _in_section(sections.get("in_progress", [])):
        new_status = "in_progress"
    elif _in_section(sections.get("done", [])):
        new_status = "done"
        for item in sections.get("done", []):
            if _normalize_for_match(item) == needle:
                target["result_line"] = item.split("\n")[0][:200]
                break
    elif _in_section(sections.get("failed", [])):
        new_status = "failed"
        for item in sections.get("failed", []):
            if _normalize_for_match(item) == needle:
                target["result_line"] = item.split("\n")[0][:200]
                break
    else:
        # Not found in any section
        if prev_status == "in_progress":
            new_status = "done"  # archived after completion (inferred; see below)
        else:
            new_status = "removed"

    # The durable OutcomeStore is the authoritative source of terminal status.
    # It overrides the missions.md section scan / absence inference above, which
    # mis-reports a pruned/renamed/crashed mission as "done" (issue #2285).
    #
    # But only when the mission is NOT currently live: a mission requeued after a
    # prior terminal run shares its canonical_mission_key, so a stale outcome row
    # must never override a fresh pending/in_progress state. The live section scan
    # wins for those; the outcome log only speaks once the mission has left them.
    outcome = None
    if new_status not in ("pending", "in_progress"):
        outcome = _authoritative_outcome(instance_dir, stored_text)
    if outcome and outcome["status"] in ("done", "failed"):
        new_status = outcome["status"]
        target["outcome"] = outcome
        if outcome.get("detail") and not target.get("result_line"):
            target["result_line"] = outcome["detail"][:200]
    else:
        target.setdefault("outcome", None)

    target["status"] = new_status
    records[target_idx] = target
    _save_index(instance_dir, records)

    # Resolve+attach a structured result exactly once, on the terminal transition.
    if (
        new_status in ("done", "failed")
        and target.get("result") is None
        and not target.get("result_ref")
    ):
        try:
            from app.api.mission_results import (
                always_inline_keys,
                resolve_mission_result,
            )
            resolved = resolve_mission_result(instance_dir, target.get("text", ""))
            if resolved is not None:
                attach_result(
                    instance_dir, mission_id, resolved,
                    always_inline=always_inline_keys(target.get("text", "")),
                )
                target = get_mission(instance_dir, mission_id) or target
        except Exception as e:
            log.error("result resolution failed for %s: %s", mission_id, e)

    return _with_result_defaults(target)


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


_PROJECT_TAG = re.compile(r"\[project:[^\]]+\]")


def _normalize_loose(text: str) -> str:
    """Like _normalize_for_match but also strips the [project:...] tag,
    so a title with the tag already removed still matches a stored entry."""
    text = _normalize_for_match(text)
    return _PROJECT_TAG.sub("", text).strip()


# Match precedence: in_progress beats pending beats terminal states.
_STATUS_RANK = {"in_progress": 0, "pending": 1}


def find_active_mission_id(instance_dir: Path, text: str) -> Optional[str]:
    """Resolve a mission title to its API mission id via the sidecar index.

    Returns the id of the best-matching record (in_progress preferred, then
    pending, then most-recently-created), or None when nothing matches.
    """
    needle = _normalize_loose(text)
    if not needle:
        return None
    candidates = [
        rec for rec in _load_index(instance_dir)
        if _normalize_loose(rec.get("text", "")) == needle
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: (_STATUS_RANK.get(r.get("status"), 2), -r.get("created", 0.0))
    )
    return candidates[0].get("id")


def update_mission_text(instance_dir: Path, mission_id: str, new_text: str) -> bool:
    """Update the stored text for a pending mission in the sidecar index."""
    records = _load_index(instance_dir)
    for i, rec in enumerate(records):
        if rec.get("id") == mission_id:
            if rec.get("status") != "pending":
                return False
            records[i]["text"] = new_text
            _save_index(instance_dir, records)
            return True
    return False


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
