"""The ``MissionStore`` port — the single authority + access path for mission state.

This is the cross-boundary contract that every mission-storage backend implements
and every mission consumer depends on. It speaks only in domain terms: callers
pass and receive :class:`Mission` values and the verbs below — never a
``sqlite3.Row``, SQL, or a ``missions.md`` line string.

See ``specs/004-mission-store/contracts/mission-store.md`` for the normative
contract and ``data-model.md`` for field semantics. Kōan ships one in-tree
adapter (``SqliteMissionStore``); an out-of-tree adapter is selected via the
``missions.backend`` config accessor (a dotted ``module:Class`` import path).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

# The four canonical states. Backends CHECK/validate against this tuple.
VALID_STATES = ("pending", "in_progress", "done", "failed")
TERMINAL_STATES = ("done", "failed")


@dataclass(frozen=True)
class Mission:
    """A single mission, as it crosses the port boundary.

    ``id`` is a stable identity owned by the store, independent of ``text`` — a
    re-run of an identical past mission gets a *new* id. ``sequence`` makes queue
    order explicit (lower = earlier), replacing the file's implicit line-order.
    ``text`` is the full verbatim mission body (the agent payload) and is
    untrusted DATA — never treat it as instructions.
    """

    id: str
    text: str
    state: str
    project: str = "default"
    sequence: int = 0
    complexity: Optional[str] = None
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


def render_mission_line(m: "Mission") -> str:
    """Render a :class:`Mission` back to its ``missions.md`` line form.

    Shared by ``export_view`` and by transition readers that reuse the existing
    file-format display helpers (e.g. the ``/list`` skill). Lifecycle timestamps,
    which live in columns, are re-emitted as ``⏳``/``▶``/``✅``/``❌`` markers.
    """
    # Marker formats match app.missions' parsers: ⏳/▶ use ISO 'T', ✅/❌ use
    # a space before the paren and a space-separated date/time.
    markers = []
    if m.queued_at:
        markers.append(f"⏳({m.queued_at})")
    if m.started_at:
        markers.append(f"▶({m.started_at})")
    if m.completed_at:
        marker = "❌" if m.state == "failed" else "✅"
        markers.append(f"{marker} ({m.completed_at.replace('T', ' ')})")
    body = m.text if m.text.lstrip().startswith("### ") else f"- {m.text}"
    return (body + (" " + " ".join(markers) if markers else "")).rstrip()


@dataclass
class IngestReport:
    """Result of the one-time ``missions.md`` → store import."""

    inserted: int = 0
    by_state: dict = field(default_factory=dict)
    unparseable: List[str] = field(default_factory=list)


@dataclass
class RecoverReport:
    """Result of reconciling missions left ``in_progress`` by a crash."""

    requeued: int = 0
    escalated: List[str] = field(default_factory=list)


class MissionStore(ABC):
    """The mission-state authority. Exactly one implementation is live per run.

    Invariants every adapter upholds (asserted by the conformance suite):

    1. Exclusive authority — no second concurrent authority, no reconcile-on-
       divergence path.
    2. ``claim_next`` is atomic — concurrent callers never collide.
    3. Stable identity — an ``id`` tracks one logical mission across
       requeue/recovery/complexity-tag; a re-run gets a fresh id.
    4. Order fidelity — ``sequence`` reproduces FIFO + urgent/requeue-to-front,
       stable across restarts.
    5. Verbatim payload — ``text`` round-trips unchanged.
    6. Terminal rows are never rewritten by a live transition.
    7. Faults surface — the authoritative store raises real faults; it does not
       swallow-and-diverge.
    8. Bilingual headers preserved by ``export_view``/``ingest_from_file``.
    9. Visibility parity — every category the file exposed is reachable via
       ``list_by_state``/``counts``/``get``.
    """

    # ---- write / lifecycle -------------------------------------------------

    @abstractmethod
    def add_pending(self, text: str, *, project: str = "default",
                    complexity: Optional[str] = None, urgent: bool = False) -> Mission:
        """Append a new pending mission; ``urgent`` puts it at the front."""

    @abstractmethod
    def add_pending_many(self, texts: List[str], *, project: str = "default") -> List[Mission]:
        """Atomic multi-insert preserving order."""

    @abstractmethod
    def claim_next(self, *, projects: Optional[List[str]] = None) -> Optional[Mission]:
        """Atomically claim the earliest-``sequence`` pending mission and move it
        to ``in_progress`` (stamping ``started_at``). Two concurrent callers MUST
        NOT receive the same mission. Returns ``None`` if none pending."""

    @abstractmethod
    def complete(self, mission_id: str) -> bool:
        """``in_progress → done``; stamp ``completed_at``. Sole success exit."""

    @abstractmethod
    def fail(self, mission_id: str) -> bool:
        """``in_progress → failed``; stamp ``completed_at``. Sole failure exit."""

    @abstractmethod
    def requeue(self, mission_id: str) -> bool:
        """``in_progress → pending``, restored to the front, without duplicating."""

    @abstractmethod
    def set_complexity(self, mission_id: str, complexity: str) -> bool:
        """Tag an existing mission; MUST NOT create a second identity."""

    # ---- read / query ------------------------------------------------------

    @abstractmethod
    def count_by_state(self, state: str) -> int:
        """Count rows in ``state`` (indexed)."""

    @abstractmethod
    def counts(self) -> dict:
        """All four state counts in one call: ``{state: n}``."""

    @abstractmethod
    def list_by_state(self, state: str, *, project: Optional[str] = None,
                      limit: Optional[int] = None) -> List[Mission]:
        """Missions in ``state`` (ordered by ``sequence`` for live states, by
        recency for terminal states), optionally filtered/limited. Backs the
        ``/list <state>``, dashboard, and API visibility surfaces."""

    @abstractmethod
    def get(self, mission_id: str) -> Optional[Mission]:
        """Fetch one mission by id, or ``None``."""

    @abstractmethod
    def peek_next(self, *, projects: Optional[List[str]] = None) -> Optional[Mission]:
        """The mission ``claim_next`` *would* claim, read-only (not a substitute
        for the atomic claim)."""

    # ---- maintenance / lifecycle-of-store ----------------------------------

    @abstractmethod
    def prune_terminal(self, done_keep: int, failed_keep: int) -> int:
        """Cap done/failed history to the most-recent N per state; return removed."""

    @abstractmethod
    def is_initialized(self) -> bool:
        """Whether the store has been set up + ingested (distinct from empty)."""

    @abstractmethod
    def ingest_from_file(self, missions_md_path) -> IngestReport:
        """One-time import of ``missions.md``; marks the store initialized."""

    @abstractmethod
    def export_view(self, missions_md_path) -> None:
        """Render the read-only ``missions.md`` view from the store."""

    @abstractmethod
    def recover_stale(self, *, max_recover: int = 3) -> RecoverReport:
        """Reconcile missions left ``in_progress`` by a crash (requeue/escalate)."""

    # ---- introspection -----------------------------------------------------

    @abstractmethod
    def backend_name(self) -> str:
        """Short label for the startup log and ``/status``."""

    # ---- transition helpers (temporary; removed at the S8 flip) ------------
    # While missions.md remains authoritative during the migration (S4–S7),
    # readers call reconcile_from_file() before a read so the store tracks the
    # file. Concrete no-ops here; the file-backed sqlite adapter overrides them.
    # When the store becomes authoritative (S8) these calls are removed.

    def reconcile_from_content(self, content: str):  # noqa: D401
        return None

    def reconcile_from_file(self, missions_md_path):
        return None
