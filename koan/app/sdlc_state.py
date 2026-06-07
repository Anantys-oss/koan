"""Kōan — SDLC State Persistence Layer

Manages per-workflow state for the /sdlc multi-phase orchestration skill.
Each SDLC run for a given issue lives in its own workspace under
``instance/sdlc/{issue_name}/`` with:

- ``STATE.json``     — phase tracking, metadata, approval flag
- ``RESEARCH.md``    — research agent output
- ``ADR.md``         — architecture decision record
- ``PLAN.md``        — implementation plan (human reviews this before /approve)
- ``IMPLEMENTATION.md`` — implementation diff summary
- ``SECURITY.md``    — security review verdict
- ``QA.md``          — QA review verdict
- ``SRE.md``         — SRE review verdict
- ``REVIEW.md``      — aggregated review summary
- ``DOCS.md``        — documentation update summary

Concurrent SDLC runs for the same issue_name will race on STATE.json.
``save_sdlc_state`` uses ``atomic_write_json`` (temp + rename + fcntl.flock)
which serializes writers at the OS level — last writer wins.

State files accumulate indefinitely; call ``archive_sdlc_workspace()`` to
move a terminal workspace (PRODUCTION_READY or ABANDONED) to
``instance/sdlc/_archived/``.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import atomic_write_json


# Artifact file names produced/consumed by each SDLC phase.
SDLC_ARTIFACTS = [
    "RESEARCH.md",
    "ADR.md",
    "PLAN.md",
    "IMPLEMENTATION.md",
    "SECURITY.md",
    "QA.md",
    "SRE.md",
    "REVIEW.md",
    "DOCS.md",
]

# JSON state file inside each workspace.
_STATE_FILENAME = "STATE.json"

# Max SDLC fix iterations before the orchestrator gives up.
MAX_FIX_ITERATIONS = 3


class SdlcPhase(str, Enum):
    """Ordered phases of an SDLC workflow run."""

    RESEARCH = "research"
    ARCHITECTURE = "architecture"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    FIX_LOOP = "fix_loop"
    DOCUMENTATION = "documentation"
    PRODUCTION_READY = "production_ready"
    ABANDONED = "abandoned"

    @property
    def is_terminal(self) -> bool:
        return self in (SdlcPhase.PRODUCTION_READY, SdlcPhase.ABANDONED)


class SdlcRiskLevel(str, Enum):
    """Assessed risk level for an SDLC workflow."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass
class SdlcState:
    """Runtime state for one SDLC workflow run.

    Serialised to / deserialised from STATE.json inside the workspace.
    """

    issue_name: str
    description: str
    current_phase: SdlcPhase
    risk_level: SdlcRiskLevel = SdlcRiskLevel.MEDIUM
    fix_iteration: int = 0
    failing_experts: List[str] = field(default_factory=list)
    approved: bool = False
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    artifact_checksums: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "issue_name": self.issue_name,
            "description": self.description,
            "current_phase": self.current_phase.value,
            "risk_level": self.risk_level.value,
            "fix_iteration": self.fix_iteration,
            "failing_experts": list(self.failing_experts),
            "approved": self.approved,
            "started_at": self.started_at,
            "artifact_checksums": dict(self.artifact_checksums),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SdlcState":
        try:
            phase = SdlcPhase(data.get("current_phase", "research"))
        except ValueError:
            phase = SdlcPhase.RESEARCH

        try:
            risk = SdlcRiskLevel(data.get("risk_level", "Medium"))
        except ValueError:
            risk = SdlcRiskLevel.MEDIUM

        return cls(
            issue_name=data.get("issue_name", ""),
            description=data.get("description", ""),
            current_phase=phase,
            risk_level=risk,
            fix_iteration=int(data.get("fix_iteration", 0)),
            failing_experts=list(data.get("failing_experts", [])),
            approved=bool(data.get("approved", False)),
            started_at=data.get(
                "started_at",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
            artifact_checksums=dict(data.get("artifact_checksums", {})),
        )


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


def get_sdlc_workspace(instance_dir: str, issue_name: str) -> Path:
    """Return the workspace directory for *issue_name*, creating it if absent."""
    ws = Path(instance_dir) / "sdlc" / _sanitise_issue_name(issue_name)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def load_sdlc_state(instance_dir: str, issue_name: str) -> Optional[SdlcState]:
    """Load the SDLC state for *issue_name*.

    Returns ``None`` cleanly if the workspace is absent or STATE.json is
    missing or malformed — callers should treat ``None`` as "not started".
    """
    ws = Path(instance_dir) / "sdlc" / _sanitise_issue_name(issue_name)
    state_file = ws / _STATE_FILENAME
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return SdlcState.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def save_sdlc_state(instance_dir: str, state: SdlcState) -> None:
    """Persist *state* to STATE.json atomically.

    Creates the workspace directory if absent. Uses ``atomic_write_json``
    (temp-file + os.replace + fcntl.flock) so a crash mid-write never
    leaves a partial file.
    """
    ws = get_sdlc_workspace(instance_dir, state.issue_name)
    state_file = ws / _STATE_FILENAME
    atomic_write_json(state_file, state.to_dict(), indent=2)


def get_artifact_path(
    instance_dir: str, issue_name: str, artifact_name: str
) -> Path:
    """Return the path to *artifact_name* inside *issue_name*'s workspace.

    The artifact need not exist yet — this is a pure path computation.
    *artifact_name* must be one of ``SDLC_ARTIFACTS`` (e.g. ``"PLAN.md"``).
    """
    ws = Path(instance_dir) / "sdlc" / _sanitise_issue_name(issue_name)
    return ws / artifact_name


def archive_sdlc_workspace(instance_dir: str, issue_name: str) -> Optional[Path]:
    """Move a terminal workspace to ``instance/sdlc/_archived/``.

    Only moves workspaces whose ``current_phase`` is PRODUCTION_READY or
    ABANDONED.  Returns the destination path, or ``None`` if the workspace
    was absent or not in a terminal phase.
    """
    ws = Path(instance_dir) / "sdlc" / _sanitise_issue_name(issue_name)
    if not ws.exists():
        return None

    state = load_sdlc_state(instance_dir, issue_name)
    if state is None or not state.current_phase.is_terminal:
        return None

    archived_root = Path(instance_dir) / "sdlc" / "_archived"
    archived_root.mkdir(parents=True, exist_ok=True)
    dest = archived_root / _sanitise_issue_name(issue_name)

    # Avoid clobbering a previous archive with the same name.
    if dest.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dest = archived_root / f"{_sanitise_issue_name(issue_name)}-{ts}"

    shutil.move(str(ws), str(dest))
    return dest


def list_sdlc_workspaces(instance_dir: str) -> List[str]:
    """Return issue names for all active (non-archived) SDLC workspaces."""
    sdlc_root = Path(instance_dir) / "sdlc"
    if not sdlc_root.exists():
        return []
    return [
        d.name
        for d in sorted(sdlc_root.iterdir())
        if d.is_dir() and d.name != "_archived"
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitise_issue_name(issue_name: str) -> str:
    """Normalise *issue_name* for use as a directory component.

    Replaces characters unsafe in file paths with underscores and strips
    leading/trailing whitespace, dots, and underscores.
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in issue_name.strip())
    return safe.strip("._") or "unnamed"
