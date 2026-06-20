"""
Kōan — Structured mission store (data/view split)

This module implements the canonical mission store as a structured JSON file
(``instance/missions.json``) with a generated human/LLM-readable Markdown view
(``instance/missions.md``).

Architecture
------------
- ``instance/missions.json`` is the single source of truth.  Every mutating
  method writes JSON atomically (via :func:`app.utils.atomic_write`) and then
  regenerates ``missions.md`` from scratch.
- ``instance/missions.md`` is a *view*.  It must never be mutated directly by
  code; only :meth:`MissionStore._save` writes it.  Humans may edit it, but
  those edits are reconciled back into JSON on the next write.
- Human edits to ``missions.md`` are detected by comparing
  ``sha256(missions.md content)`` against a hash stored alongside the JSON.
  When the hash diverges, :meth:`MissionStore._save` calls
  :meth:`MissionStore._reconcile_from_markdown` before persisting, so no human
  edit is silently discarded.

Locking
-------
A sidecar lock file (``_STORE_LOCK_FILENAME`` = ``".missions-store.lock"``) is
held exclusively across the full load→mutate→save cycle, matching the
convention used by :func:`app.locked_file.locked_json_modify`.  The store carries its own
module-level in-process thread lock (``_STORE_LOCK``) paired with this file
lock, so concurrent mutations from multiple threads and processes are both
serialized.

Migration
---------
When ``missions.json`` does not exist yet, :meth:`MissionStore.__init__` calls
:meth:`MissionStore._migrate_in_place` to seed the JSON from the current
``missions.md`` content.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.utils import atomic_write, instance_dir
from collections.abc import Generator
from typing import Any

# Module-level in-process lock. Held alongside the per-instance file lock so
# threads within the same process do not race on the JSON store.
_STORE_LOCK = threading.Lock()

# Filenames for key store files in the instance directory.
_STORE_FILENAME = "missions.json"
_STORE_LOCK_FILENAME = ".missions-store.lock"
_MARKDOWN_FILENAME = "missions.md"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_TS_FORMAT = "%Y-%m-%dT%H:%M"

# Patterns for extracting lifecycle markers during migration
_QUEUED_PATTERN = re.compile(r"\s*⏳\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\)")
_STARTED_PATTERN = re.compile(r"\s*▶\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\)")
_COMPLETED_PATTERN = re.compile(
    r"\s*[✅❌]\s*\((\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\)"
)
# [r:N] crash-recovery counter
_CRASH_COUNT_RE = re.compile(r"\s*\[r:(\d+)\]")
# [s:N] stagnation-retry counter
_STAGNATION_COUNT_RE = re.compile(r"\s*\[s:(\d+)\]")
# [complexity:X]
_COMPLEXITY_RE = re.compile(r"\s*\[complexity:([a-zA-Z]+)\]")
# [flushed], [stagnation] — add new tag names here to survive Markdown round-trips
_KNOWN_TAG_RE = re.compile(r"\[(flushed|stagnation)\]", re.IGNORECASE)

# Section render order and display names for to_markdown().
# Each tuple is (status_key, header_text); order defines the rendered section order.
_VIEW_SECTIONS: list = [
    ("in_progress", "In Progress"),
    ("pending", "Pending"),
    ("done", "Done"),
    ("failed", "Failed"),
]

# Valid status values for MissionRecord.status — used by __post_init__ to reject typos.
_VALID_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "done", "failed"})

# Caps applied in the generated view (not in the JSON store)
_DONE_CAP = 50
_FAILED_CAP = 30


@dataclass(frozen=True)
class MissionRecord:
    """A single mission with all lifecycle state stored as typed fields.

    The ``id`` is a stable UUID assigned once at creation and never changed —
    it survives requeues, retries, and renames.

    ``text`` is the clean canonical text with *no* lifecycle markers
    (no ``⏳``, ``▶``, ``✅/❌``, ``[r:N]``, ``[complexity:X]``).  Markers
    are re-rendered into ``missions.md`` by :meth:`MissionStore._render_record`.

    The ``tags`` tuple holds arbitrary string labels such as ``"flushed"`` and
    ``"stagnation"`` that appear after the completion timestamp in the Markdown
    view (e.g. ``❌ (2026-06-14 20:00) [flushed]``).

    The dataclass is frozen so external callers cannot mutate fields without
    going through the store's mutation API.  Internal store methods that need
    to update fields use ``object.__setattr__(record, "field", value)``.
    """

    id: str                        # UUID — stable across entire lifecycle
    text: str                      # Clean text; NO lifecycle markers
    status: str                    # "pending" | "in_progress" | "done" | "failed"
    project: str                   # "" if unset (always str, never None)
    queued_at: str | None          # ISO8601 "YYYY-MM-DDTHH:MM" or None
    started_at: str | None
    completed_at: str | None
    tags: tuple[str, ...]          # e.g. ("flushed", "stagnation")
    complexity: str | None         # None | "trivial" | "simple" | "medium" | "complex"
    crash_count: int               # crash-recovery requeue count ([r:N] in Markdown)
    stagnation_count: int          # stagnation-retry requeue count ([s:N] in Markdown)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {self.status!r}; must be one of {sorted(_VALID_STATUSES)}"
            )

    def display_title(self, max_length: int = 120) -> str:
        """Return a display-formatted title: '[project] text', truncated.

        Strips trailing origin markers (📬, 🎫) from text and converts the
        project field to a leading ``[project]`` prefix.  Safe to call on
        any record regardless of status.
        """
        text = self.text
        for marker in ("📬", "🎫"):
            if text.endswith(marker):
                text = text[: -len(marker)].rstrip()
                break
        if self.project:
            text = f"[{self.project}] {text}"
        if len(text) > max_length:
            text = text[: max_length - 3] + "..."
        return text

    def origin_marker(self) -> str:
        """Return the trailing origin marker embedded in ``text``, or ``''``.

        Returns ``'📬'`` for GitHub-sourced missions and ``'🎫'`` for
        Jira-sourced missions.  Returns an empty string for all others.
        """
        for marker in ("📬", "🎫"):
            if self.text.endswith(marker):
                return marker
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON persistence."""
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "project": self.project,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tags": list(self.tags),
            "complexity": self.complexity,
            "crash_count": self.crash_count,
            "stagnation_count": self.stagnation_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MissionRecord":
        """Deserialize from a dict loaded from JSON."""
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            text=d.get("text", ""),
            status=d.get("status", "pending"),
            project=d.get("project", ""),
            queued_at=d.get("queued_at"),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            tags=tuple(d.get("tags", [])),
            complexity=d.get("complexity"),
            crash_count=int(d.get("crash_count", 0)),
            stagnation_count=int(d.get("stagnation_count", 0)),
        )


def _view_hash(content: str) -> str:
    """Return the sha256 hex digest of ``content`` (the Markdown view)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MissionStore:
    """Structured mission store backed by ``instance/missions.json``.

    Construct with ``MissionStore()`` — the store loads from disk automatically.
    Mutate through the public methods; :func:`locked_store` handles the
    atomic load→mutate→save cycle for all writes.  The store holds all records
    in memory as an ordered list (``_records``); ordering within a status group
    reflects queue position (pending[0] is next to run).

    Thread/process safety
    ---------------------
    All mutating operations that persist to disk must hold the exclusive lock on
    the sidecar lock file for the duration of load→mutate→save.  The canonical
    entry point is :func:`locked_store`, which acquires the lock, loads the
    store, yields it to the caller, then calls :meth:`_save` on clean exit.
    Convenience wrappers like :meth:`start`, :meth:`complete`, and :meth:`fail`
    each perform their own locked load→mutate→save cycle internally when called
    on a *fresh* store object.

    For performance-critical callers that need to hold the lock across multiple
    mutations, acquire it manually via :meth:`_lock_path` before instantiating.
    """

    def __init__(self) -> None:
        # Ordered list of all records (across all statuses).
        # Within each status the list order defines queue position.
        self._records: list[MissionRecord] = []
        # Ideas backlog (the ``## Ideas`` section). Each item is the idea
        # content with the leading ``- `` stripped. Ideas are never picked up
        # by the agent loop; the store owns them only so to_markdown() does
        # not destroy the section when it regenerates missions.md.
        self._ideas: list[str] = []
        # sha256 of the last missions.md content we wrote, used to detect
        # human edits between save() calls.
        self._last_view_hash: str | None = None
        self._load()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _store_path(self) -> Path:
        """Absolute path to ``instance/missions.json``."""
        return Path(instance_dir(), _STORE_FILENAME)

    def _view_path(self) -> Path:
        """Absolute path to ``instance/missions.md``."""
        return Path(instance_dir(), _MARKDOWN_FILENAME)

    def _lock_path(self) -> Path:
        """Sidecar lock file path for the JSON store."""
        return Path(instance_dir(), _STORE_LOCK_FILENAME)

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load the store from ``missions.json``, or migrate from ``missions.md``.

        Called automatically by ``__init__``.  If ``missions.json`` does not
        exist, falls back to :meth:`_migrate_in_place` to seed the JSON store
        from the existing Markdown file.  Not lock-safe on its own — callers
        that need atomic load-then-mutate semantics must use :func:`locked_store`.
        """
        store_path = self._store_path()

        if not store_path.exists():
            # First run or pre-migration instance — seed from missions.md
            self._migrate_in_place()
            return

        try:
            raw = json.loads(store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupted JSON — migrate from Markdown as fallback
            self._migrate_in_place()
            return

        for item in raw.get("records", []):
            self._records.append(MissionRecord.from_dict(item))

        self._ideas = list(raw.get("ideas", []))
        self._last_view_hash = raw.get("view_hash")

        # Detect human edits to missions.md since the last save
        view_path = self._view_path()
        if view_path.exists():
            current_content = view_path.read_text(encoding="utf-8")
            current_hash = _view_hash(current_content)
            if self._last_view_hash is not None and current_hash != self._last_view_hash:
                # Human edited the Markdown view — reconcile back into the store
                self._reconcile_from_markdown(current_content)

    def _save(self) -> None:
        """Persist the store to ``missions.json`` and regenerate ``missions.md``.

        Performs an atomic write (temp file + rename) for both files so a
        crash between the two writes leaves at worst a stale ``missions.md``
        view, which will be regenerated from JSON on the next load.

        The generated ``missions.md`` is compatible with all existing parsers
        (``parse_sections()``, ``extract_project_tag()``, etc.).
        """
        view_content = self._to_markdown()
        view_hash = _view_hash(view_content)
        self._last_view_hash = view_hash

        payload: dict[str, Any] = {
            "records": [r.to_dict() for r in self._records],
            "ideas": self._ideas,
            "view_hash": view_hash,
        }

        # Ensure parent directory exists
        self._store_path().parent.mkdir(parents=True, exist_ok=True)

        # Write JSON first (source of truth)
        atomic_write(self._store_path(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

        # Regenerate the Markdown view
        atomic_write(self._view_path(), view_content)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def find(self, text: str) -> MissionRecord | None:
        """Find a record by canonical key match.

        Uses :func:`app.missions.canonical_mission_key` to strip lifecycle
        markers from both *text* and stored texts before comparing, so a
        mission can be found regardless of which markers are currently attached.

        Args:
            text: Mission text (may include or omit lifecycle markers).

        Returns:
            The matching :class:`MissionRecord`, or ``None`` if not found.
        """
        from app.missions import canonical_mission_key  # lazy import

        needle = canonical_mission_key(text)
        for record in self._records:
            if canonical_mission_key(record.text) == needle:
                return record
        return None

    def get_by_status(self, status: str) -> list[MissionRecord]:
        """Return all records with the given *status* in queue order.

        Args:
            status: One of ``"pending"``, ``"in_progress"``, ``"done"``,
                    or ``"failed"``.

        Returns:
            List of matching :class:`MissionRecord` instances.  The order
            reflects insertion order within that status group.
        """
        return [r for r in self._records if r.status == status]

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def _now_iso(self) -> str:
        """Return current local time as ``YYYY-MM-DDTHH:MM``."""
        return time.strftime(_TS_FORMAT)

    def _now_display(self) -> str:
        """Return current local time as ``YYYY-MM-DD HH:MM`` (display format)."""
        return time.strftime("%Y-%m-%d %H:%M")

    # ------------------------------------------------------------------
    # Public mutators
    # ------------------------------------------------------------------

    def add(
        self,
        text: str,
        project: str | None = None,
        complexity: str | None = None,
        *,
        urgent: bool = False,
    ) -> tuple[MissionRecord, bool]:
        """Create a new pending mission record.

        Checks for duplicates using :func:`app.missions.canonical_mission_key`.
        If a record with the same canonical key already exists in *pending* or
        *in_progress* state, returns the existing record without creating a
        duplicate.  Done/Failed records are not considered duplicates — a
        completed mission can be re-queued (e.g., re-review after new commits).

        Args:
            text: Clean mission text (no lifecycle markers).
            project: Project name, or ``None``/``""`` if untagged.
            complexity: Optional complexity tier string.
            urgent: When ``True``, insert at the top of the pending queue
                (next to be picked up) instead of the bottom (FIFO).

        Returns:
            A ``(record, was_new)`` tuple.  *record* is the new or existing
            active :class:`MissionRecord`; *was_new* is ``True`` when a new
            record was inserted and ``False`` when a duplicate was found.
        """
        from app.missions import canonical_mission_key  # lazy import

        project = project or ""

        # Strip any stray markers the caller may have passed; reuse as dedup key.
        clean_text = canonical_mission_key(text)
        existing = next(
            (r for r in self._records
             if r.status in ("pending", "in_progress")
             and canonical_mission_key(r.text) == clean_text),
            None,
        )
        if existing is not None:
            return existing, False

        record = MissionRecord(
            id=str(uuid.uuid4()),
            text=clean_text,
            status="pending",
            project=project or "",
            queued_at=self._now_iso(),
            started_at=None,
            completed_at=None,
            tags=(),
            complexity=complexity,
            crash_count=0,
            stagnation_count=0,
        )

        if urgent:
            insert_at = next(
                (i for i, r in enumerate(self._records) if r.status == "pending"),
                len(self._records),
            )
            self._records.insert(insert_at, record)
        else:
            self._records.append(record)

        return record, True

    def start(self, text: str) -> bool:
        """Move the matching pending mission to ``in_progress``.

        As a safety net, any *existing* ``in_progress`` missions are flushed
        to ``failed`` with the ``[flushed]`` tag before the new mission is
        started (mirrors the behaviour of ``_flush_in_progress_to_failed()`` in
        ``missions.py``).

        Args:
            text: Mission text used to locate the record.

        Returns:
            ``True`` if the mission was found and started, ``False`` otherwise.
        """
        record = self.find(text)
        if record is None or record.status != "pending":
            return False

        # Flush stale in-progress records (safety net — normally empty)
        for stale in self.get_by_status("in_progress"):
            object.__setattr__(stale, "status", "failed")
            object.__setattr__(stale, "completed_at", self._now_display())
            if "flushed" not in stale.tags:
                object.__setattr__(stale, "tags", (*stale.tags, "flushed"))

        object.__setattr__(record, "status", "in_progress")
        object.__setattr__(record, "started_at", self._now_iso())
        return True

    def complete(self, text: str) -> bool:
        """Move the matching in-progress mission to ``done``.

        Also accepts a mission in ``pending`` status (mirrors
        ``complete_mission()`` in ``missions.py`` which searches Pending
        before In Progress).

        Args:
            text: Mission text used to locate the record.

        Returns:
            ``True`` if the mission was found and completed, ``False`` otherwise.
        """
        record = self.find(text)
        if record is None or record.status not in ("in_progress", "pending"):
            return False

        object.__setattr__(record, "status", "done")
        object.__setattr__(record, "completed_at", self._now_display())
        return True

    def fail(self, text: str, extra_tags: list[str] | None = None) -> bool:
        """Move the matching mission to ``failed``.

        Searches ``in_progress`` and ``pending`` sections, mirroring
        ``fail_mission()`` in ``missions.py``.

        Args:
            text: Mission text used to locate the record.
            extra_tags: Optional list of tags to add (e.g. ``["stagnation"]``).

        Returns:
            ``True`` if the mission was found and failed, ``False`` otherwise.
        """
        record = self.find(text)
        if record is None or record.status not in ("in_progress", "pending"):
            return False

        object.__setattr__(record, "status", "failed")
        object.__setattr__(record, "completed_at", self._now_display())
        if extra_tags:
            new_tags = record.tags
            for tag in extra_tags:
                if tag not in new_tags:
                    new_tags = (*new_tags, tag)
            object.__setattr__(record, "tags", new_tags)
        return True

    def requeue(self, text: str, reason: str = "crash") -> bool:
        """Move any mission back to ``pending`` at the top of the queue.

        Increments either :attr:`MissionRecord.crash_count` (``reason="crash"``,
        the default) or :attr:`MissionRecord.stagnation_count`
        (``reason="stagnation"``), and clears the ``started_at`` /
        ``completed_at`` timestamps.  The requeued record is prepended to the
        beginning of the ``_records`` list so it appears first in the pending
        section (queue-top semantics).

        Args:
            text:   Mission text used to locate the record.
            reason: ``"crash"`` (default) or ``"stagnation"``.

        Returns:
            ``True`` if the mission was found and requeued, ``False`` otherwise.
        """
        record = self.find(text)
        if record is None:
            return False

        # Move to queue-top by removing and prepending
        self._records.remove(record)

        object.__setattr__(record, "status", "pending")
        object.__setattr__(record, "queued_at", self._now_iso())
        object.__setattr__(record, "started_at", None)
        object.__setattr__(record, "completed_at", None)
        if reason == "stagnation":
            object.__setattr__(record, "stagnation_count", record.stagnation_count + 1)
        else:
            object.__setattr__(record, "crash_count", record.crash_count + 1)

        # Prepend to the pending section: find the first pending record.
        # When none exist, insert after the last in-progress record (or at 0).
        first_pending = next(
            (i for i, r in enumerate(self._records) if r.status == "pending"),
            None,
        )
        if first_pending is not None:
            insert_at = first_pending
        else:
            last_ip = next(
                (i for i in reversed(range(len(self._records)))
                 if self._records[i].status == "in_progress"),
                -1,
            )
            insert_at = last_ip + 1
        self._records.insert(insert_at, record)
        return True

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _reconcile_from_markdown(self, markdown: str) -> int:
        """Update ``self._records`` to match the given Markdown content.

        Called when a human edit to ``missions.md`` is detected (hash mismatch).
        Parses the Markdown sections and reconciles against existing records:

        - Existing records whose canonical key appears in the Markdown are kept
          and their status/ordering is updated to match.
        - Text found in the Markdown that has no matching record is added as a
          new record (status inferred from section).
        - Records not found anywhere in the Markdown are left unchanged (they
          may be in-flight or the editor may not have shown them).

        Args:
            markdown: Raw content of ``missions.md`` as edited by a human.

        Returns:
            Count of newly created records (i.e. missions added by a human
            editor that were not already in the JSON store).
        """
        from app.missions import parse_ideas, parse_sections, canonical_mission_key  # lazy

        # Re-capture the Ideas backlog from the edited view so direct edits
        # (or programmatic inserts) to the ## Ideas section are not lost.
        self._ideas = [_strip_list_marker(item) for item in parse_ideas(markdown)]

        sections = parse_sections(markdown)

        new_count = 0
        # Track which record IDs we've matched so we can preserve unmatched ones
        matched_ids: set = set()
        # New ordering to replace self._records (preserves unmatched records at end)
        new_records: list[MissionRecord] = []

        for status, _ in _VIEW_SECTIONS:
            for raw_item in sections.get(status, []):
                # Use first line for key derivation (multi-line missions)
                first_line = raw_item.split("\n")[0].strip()
                if first_line.startswith("- "):
                    first_line = first_line[2:]
                ck = canonical_mission_key(first_line)
                if not ck:
                    continue

                # Try to find existing record
                existing = None
                for r in self._records:
                    if r.id in matched_ids:
                        continue
                    if canonical_mission_key(r.text) == ck:
                        existing = r
                        break

                if existing is not None:
                    matched_ids.add(existing.id)
                    # Update status to match what the Markdown says
                    object.__setattr__(existing, "status", status)
                    new_records.append(existing)
                else:
                    # New record — parse metadata from the raw line
                    record = _parse_record_from_markdown_line(raw_item, status)
                    new_records.append(record)
                    new_count += 1

        # Append records that weren't found in the Markdown (preserve their state)
        new_records.extend(r for r in self._records if r.id not in matched_ids)

        self._records = new_records
        return new_count

    # ------------------------------------------------------------------
    # View generation
    # ------------------------------------------------------------------

    def _to_markdown(self) -> str:
        """Generate the Markdown content for ``missions.md``.

        The output is compatible with all existing parsers (``parse_sections()``,
        ``extract_project_tag()``, etc.).  Section order: In Progress → Pending
        → Done (capped at 50) → Failed (capped at 30).

        Returns:
            A normalized Markdown string ready to write to ``missions.md``.
        """
        caps = {
            "done": _DONE_CAP,
            "failed": _FAILED_CAP,
        }

        lines = ["# Missions", ""]

        # Emit the Ideas backlog first (mirrors insert_idea's placement right
        # after the title). Only rendered when there are ideas to preserve.
        if self._ideas:
            lines.append("## Ideas")
            lines.append("")
            lines.extend(f"- {t}" for t in self._ideas)
            lines.append("")

        for status, header in _VIEW_SECTIONS:
            lines.append(f"## {header}")
            lines.append("")
            records = self.get_by_status(status)
            cap = caps.get(status)
            if cap is not None:
                records = records[:cap]
            lines.extend(self._render_record(r) for r in records)
            lines.append("")

        # Strip trailing blank lines, add single final newline
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    def _render_record(self, r: MissionRecord) -> str:
        """Format one record as a Markdown list item with lifecycle markers.

        The ``[project:X]`` tag is placed immediately after the dash, before
        the text, matching the legacy ``- [project:X] text …`` format produced
        throughout the codebase. As a mission moves through its lifecycle the
        earlier timestamps are retained, so later states accumulate markers.

        Format (pending):
            ``- [project:webapp] text [complexity:simple] ⏳(2026-06-14T21:00) [r:2]``

        Format (in_progress):
            ``- [project:webapp] text ⏳(2026-06-14T21:00) ▶(2026-06-14T21:30) [r:1]``

        Format (done):
            ``- [project:webapp] text ⏳(…) ▶(…) ✅ (2026-06-14 20:00)``

        Format (failed):
            ``- [project:webapp] text ⏳(…) ▶(…) ❌ (2026-06-14 19:00) [flushed]``

        Marker order matches the legacy ``missions.md`` format: ``[complexity:X]``
        is placed before the timestamps, while the ``[r:N]`` crash counter and
        ``[s:N]`` stagnation counter are appended after them (mirroring
        ``recover.py`` and ``tag_complexity_in_pending``). Parsing is
        position-independent (regex search over the whole line), so this order is
        cosmetic, not load-bearing.

        Args:
            r: The :class:`MissionRecord` to render.

        Returns:
            A ``"- ..."`` string (no trailing newline).
        """
        parts = ["-"]

        if r.project:
            parts.append(f"[project:{r.project}]")

        parts.append(r.text)

        if r.complexity:
            parts.append(f"[complexity:{r.complexity}]")

        if r.status == "pending" or r.queued_at:
            ts = r.queued_at or time.strftime(_TS_FORMAT)
            parts.append(f"⏳({ts})")
        if r.status == "in_progress" or r.started_at:
            ts = r.started_at or time.strftime(_TS_FORMAT)
            parts.append(f"▶({ts})")

        if r.crash_count > 0:
            parts.append(f"[r:{r.crash_count}]")

        if r.stagnation_count > 0:
            parts.append(f"[s:{r.stagnation_count}]")

        if r.status == "done":
            ts = r.completed_at or time.strftime("%Y-%m-%d %H:%M")
            parts.append(f"✅ ({ts})")
            parts.extend(f"[{tag}]" for tag in r.tags)
        elif r.status == "failed":
            ts = r.completed_at or time.strftime("%Y-%m-%d %H:%M")
            parts.append(f"❌ ({ts})")
            parts.extend(f"[{tag}]" for tag in r.tags)

        return " ".join(parts)

    # ------------------------------------------------------------------
    # Migration (classmethod)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Queue-manipulation helpers
    # ------------------------------------------------------------------

    def get_pending_at(self, index: int) -> "MissionRecord | None":
        """Return the *index*-th pending record (0-based), or None."""
        pending = self.get_by_status("pending")
        if 0 <= index < len(pending):
            return pending[index]
        return None

    def reorder_pending(self, from_idx: int, to_idx: int) -> bool:
        """Move a pending record from *from_idx* to *to_idx* (both 0-based).

        Indices reference positions within the pending sub-list only.

        Returns:
            ``True`` if the move was applied, ``False`` if either index is
            out of range or they are equal.
        """
        pending = self.get_by_status("pending")
        if from_idx == to_idx:
            return False
        if not (0 <= from_idx < len(pending) and 0 <= to_idx < len(pending)):
            return False

        record = pending[from_idx]
        self._records.remove(record)

        # Re-find position of to_idx in the updated list
        new_pending = self.get_by_status("pending")
        # Determine where to_idx lands in the overall _records list
        if to_idx < len(new_pending):
            target = new_pending[to_idx]
            insert_at = self._records.index(target)
        else:
            # Append after the last pending record
            non_pending_idx = next(
                (i for i, r in enumerate(self._records) if r.status != "pending"),
                len(self._records),
            )
            insert_at = non_pending_idx

        self._records.insert(insert_at, record)
        return True

    def edit(self, old_text: str, new_text: str) -> bool:
        """Replace the text of a pending record.

        Args:
            old_text: Current mission text (lifecycle-marker-agnostic lookup).
            new_text: New clean text (no lifecycle markers).

        Returns:
            ``True`` if the record was found and updated, ``False`` otherwise.

        Raises:
            ValueError: If multiple pending records match ``old_text`` (ambiguous).
        """
        from app.missions import canonical_mission_key
        needle = canonical_mission_key(old_text)
        pending_matches = [
            r for r in self._records
            if r.status == "pending" and canonical_mission_key(r.text) == needle
        ]
        if len(pending_matches) > 1:
            raise ValueError(
                f"Ambiguous match: {len(pending_matches)} pending records match the text"
            )
        record = pending_matches[0] if pending_matches else None
        if record is None:
            return False
        object.__setattr__(record, "text", canonical_mission_key(new_text) or new_text.strip())
        return True

    def cancel_pending(self, text: str) -> bool:
        """Remove a *pending* record (does not affect in_progress/done/failed).

        Args:
            text: Mission text used to locate the record.

        Returns:
            ``True`` if the pending record was found and removed.
        """
        record = self.find(text)
        if record is None or record.status != "pending":
            return False
        self._records.remove(record)
        return True

    def set_complexity(self, text: str, tier: str) -> bool:
        """Set the complexity field on a pending record.

        Args:
            text: Mission text used to locate the record.
            tier: Complexity tier string (e.g. ``"medium"``).

        Returns:
            ``True`` if the record was found and updated.
        """
        record = self.find(text)
        if record is None or record.status != "pending":
            return False
        object.__setattr__(record, "complexity", tier)
        return True

    def prune(self, done_cap: int = _DONE_CAP, failed_cap: int = _FAILED_CAP) -> int:
        """Trim old done/failed records from the store so the JSON does not grow
        without bound.

        Keeps the most recent *done_cap* done records and *failed_cap* failed
        records (matching the view caps used by :meth:`to_markdown`).

        Returns:
            Count of records removed.
        """
        removed = 0
        for status, cap in (("done", done_cap), ("failed", failed_cap)):
            group = self.get_by_status(status)
            for r in group[cap:]:
                self._records.remove(r)
                removed += 1
        return removed

    # ------------------------------------------------------------------
    # Ideas backlog (## Ideas section)
    # ------------------------------------------------------------------

    def get_ideas(self) -> list[str]:
        """Return the ideas backlog as ``"- ..."`` lines (parse_ideas format)."""
        return [f"- {t}" for t in self._ideas]

    def add_idea(self, entry: str) -> None:
        """Append an idea to the backlog.

        Args:
            entry: The idea line; a leading ``"- "`` is stripped if present.
        """
        self._ideas.append(_strip_list_marker(entry))

    def delete_idea(self, index: int) -> str | None:
        """Delete the idea at *index* (1-based).

        Returns:
            The removed idea as a ``"- ..."`` line, or ``None`` if the index
            is out of range.
        """
        if index < 1 or index > len(self._ideas):
            return None
        removed = self._ideas.pop(index - 1)
        return f"- {removed}"

    def promote_idea(self, index: int) -> str | None:
        """Promote the idea at *index* (1-based) to the top of the pending queue.

        Returns:
            The promoted idea as a ``"- ..."`` line, or ``None`` if the index
            is out of range.
        """
        if index < 1 or index > len(self._ideas):
            return None
        idea_text = self._ideas.pop(index - 1)
        self._add_pending_top(idea_text)
        return f"- {idea_text}"

    def promote_all_ideas(self) -> list[str]:
        """Promote all ideas to the pending queue (preserving order).

        Returns:
            List of promoted ideas as ``"- ..."`` lines (empty if no ideas).
        """
        if not self._ideas:
            return []
        promoted = [f"- {t}" for t in self._ideas]
        # Insert in reverse so the first idea ends up on top of pending
        for idea_text in reversed(self._ideas):
            self._add_pending_top(idea_text)
        self._ideas = []
        return promoted

    def _add_pending_top(self, idea_text: str) -> None:
        """Add *idea_text* as a pending mission at the top of the queue.

        The idea text may carry a ``[project:X]`` tag, which is split out into
        the record's project field.
        """
        from app.utils import parse_project

        project, clean = parse_project(idea_text)
        self.add(clean, project, urgent=True)

    # ------------------------------------------------------------------
    # Migration (classmethod)
    # ------------------------------------------------------------------

    def _migrate_in_place(self) -> None:
        """Seed self by parsing the existing ``missions.md``.

        Reads the Markdown file, parses all sections, and populates ``self``
        with typed records.  Saves the resulting JSON immediately so subsequent
        loads use the fast JSON path.  Called by :meth:`_load` when
        ``missions.json`` is absent or corrupt.
        """
        from app.missions import parse_ideas, parse_sections  # lazy import

        view_path = self._view_path()

        if view_path.exists():
            try:
                content = view_path.read_text(encoding="utf-8")
            except OSError:
                content = ""
        else:
            content = ""

        if not content.strip():
            # Nothing to migrate — save to create missions.json so subsequent
            # loads skip migration entirely rather than re-entering this path.
            self._save()
            return

        # Preserve the Ideas backlog across migration
        self._ideas = [_strip_list_marker(item) for item in parse_ideas(content)]

        sections = parse_sections(content)

        for status, _ in _VIEW_SECTIONS:
            for raw_item in sections.get(status, []):
                record = _parse_record_from_markdown_line(raw_item, status)
                if record.text and "~~" not in record.text:
                    self._records.append(record)

        # Persist the migrated store immediately
        self._save()


# ---------------------------------------------------------------------------
# Public convenience: locked load→mutate→save transaction
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def locked_store() -> Generator[MissionStore, None, None]:
    """Context manager for an atomic load → mutate → save transaction.

    Acquires both the in-process thread lock and the per-instance file lock,
    then yields a freshly loaded :class:`MissionStore`.  On clean exit the
    store is saved.  On exception from the caller's block, the save is
    skipped so on-disk state is left unchanged.  Note: if :meth:`_save`
    itself raises, the exception propagates and the lock is still released
    via ``finally``, but on-disk state may be partially updated.

    Usage::

        with locked_store() as store:
            store.start("Fix bug")

    This is the canonical entry point for all mission-queue mutations:
    ``missions.md`` is regenerated from the store on save and is never
    written directly.
    """
    lock_path = Path(instance_dir()) / _STORE_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _STORE_LOCK:
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                store = MissionStore()
                yield store
                store._save()   # persist mutation first; prune is best-effort
                try:
                    store.prune()   # keep JSON bounded; runs only on clean exit
                    store._save()   # re-save after trimming history
                except Exception as _prune_err:  # prune failure must not undo the mutation
                    import sys
                    print(f"[mission_store] prune error (mutation already saved): {_prune_err}", file=sys.stderr)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_list_marker(text: str) -> str:
    """Strip a single leading ``"- "`` list marker (used for idea lines)."""
    text = text.strip()
    if text.startswith("- "):
        text = text[2:]
    return text


def _strip_all_markers(text: str) -> str:
    """Strip all known lifecycle markers from a mission line.

    Removes:
    - ``⏳(timestamp)`` queued marker
    - ``▶(timestamp)`` started marker
    - ``✅ (timestamp)`` / ``❌ (timestamp)`` completion markers
    - ``[r:N]`` crash-recovery counter
    - ``[complexity:X]`` complexity tag
    - ``[flushed]`` / ``[stagnation]`` fate tags
    - Leading ``"- "`` prefix
    - Leading ``"### "`` complex-block header prefix

    Returns the clean text suitable for storing in :attr:`MissionRecord.text`.
    """
    # Strip leading list marker or complex-block header prefix
    text = text.strip()
    if text.startswith("- "):
        text = text[2:]
    elif text.startswith("### "):
        text = text[4:]

    # Truncate at the ⏳ marker position — everything from there onwards is
    # lifecycle metadata (mirrors requeue_mission() logic in missions.py).
    queued_pos = text.find("⏳")
    if queued_pos > 0:
        text = text[:queued_pos].rstrip()

    # Remove individual patterns (belt-and-suspenders after the truncation)
    text = _QUEUED_PATTERN.sub("", text)
    text = _STARTED_PATTERN.sub("", text)
    text = _COMPLETED_PATTERN.sub("", text)
    text = _CRASH_COUNT_RE.sub("", text)
    text = _STAGNATION_COUNT_RE.sub("", text)
    text = _COMPLEXITY_RE.sub("", text)
    text = _KNOWN_TAG_RE.sub("", text)

    return text.strip()


def _parse_record_from_markdown_line(raw_item: str, status: str) -> MissionRecord:
    """Parse a raw Markdown item into a :class:`MissionRecord`.

    Extracts timestamps, project tag, complexity, crash count, and fate tags
    from lifecycle markers embedded in the raw text.  Stores only the clean
    text in :attr:`MissionRecord.text`.

    Args:
        raw_item: Raw mission item string from ``parse_sections()`` output.
        status: The section this item was found in (``"pending"``, etc.).

    Returns:
        A freshly constructed :class:`MissionRecord`.
    """
    from app.missions import extract_project_tag  # lazy import

    # Work with the first line for timestamp extraction (multi-line missions
    # only have markers on the first line)
    first_line = raw_item.split("\n")[0].strip()

    # --- timestamps ---
    queued_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    m = _QUEUED_PATTERN.search(first_line)
    if m:
        queued_at = m.group(1)

    m = _STARTED_PATTERN.search(first_line)
    if m:
        started_at = m.group(1)

    m = _COMPLETED_PATTERN.search(first_line)
    if m:
        # Store as display format (space-separated) so _render_record outputs
        # a string that _COMPLETED_PATTERN in missions.py can re-parse.
        completed_at = f"{m.group(1)} {m.group(2)}"

    # --- project tag ---
    project_raw = extract_project_tag(first_line)
    project = "" if project_raw == "default" else project_raw

    # --- complexity ---
    complexity: str | None = None
    cm = _COMPLEXITY_RE.search(first_line)
    if cm:
        complexity = cm.group(1).lower()

    # --- crash count ---
    crash_count = 0
    rm = _CRASH_COUNT_RE.search(first_line)
    if rm:
        crash_count = int(rm.group(1))

    # --- stagnation count ---
    stagnation_count = 0
    sm = _STAGNATION_COUNT_RE.search(first_line)
    if sm:
        stagnation_count = int(sm.group(1))

    # --- fate tags (flushed, stagnation) ---
    tags: list[str] = []
    for tag_m in _KNOWN_TAG_RE.finditer(first_line):
        tag = tag_m.group(1).lower()
        if tag not in tags:
            tags.append(tag)

    # --- clean text ---
    clean_text = _strip_all_markers(first_line)
    # Remove project tag from text so it's not duplicated
    # (the project field carries it separately)
    if project:
        # Strip [project:X] and [projet:X] from the clean text
        clean_text = re.sub(
            rf'\[projec?t:{re.escape(project)}\]\s*',
            '',
            clean_text,
            flags=re.IGNORECASE,
        ).strip()

    return MissionRecord(
        id=str(uuid.uuid4()),
        text=clean_text,
        status=status,
        project=project,
        queued_at=queued_at,
        started_at=started_at,
        completed_at=completed_at,
        tags=tuple(tags),
        complexity=complexity,
        crash_count=crash_count,
        stagnation_count=stagnation_count,
    )
