"""Terminal-outcome classification and recording for the mission state machine.

``classify_failure`` maps the agent loop's coarse exit signals to a stable,
machine-readable ``reason_category`` surfaced on the REST API. ``record_outcome``
is the single façade the run loop calls to append to the durable OutcomeStore.
"""

from __future__ import annotations

from typing import Optional

# Canonical categories exposed on GET /v1/missions/{id}.outcome.reason_category
REASON_CATEGORIES = ("quota", "timeout", "tool_error", "agent_error",
                     "cancelled", "stagnation")


def classify_failure(exit_code: int, *, stagnated: bool = False,
                     cause_tag: str = "") -> Optional[str]:
    """Return a reason_category for a terminal transition, or None on success."""
    if exit_code == 0:
        return None
    if stagnated or (cause_tag or "").startswith("stagnation"):
        return "stagnation"
    # SIGTERM/SIGKILL from the mission-timeout watchdog surface as 143/137.
    if exit_code in (143, 137):
        return "timeout"
    # No finer signal available at finalization; the agent loop failed the run.
    return "agent_error"


def record_outcome(instance: str, mission_text: str, status: str,
                   reason_category: Optional[str] = None,
                   detail: Optional[str] = None) -> bool:
    """Append an authoritative terminal outcome to the durable OutcomeStore."""
    from app.mission_store.aux_stores import OutcomeStore
    return OutcomeStore(instance).record(
        mission_text, status, reason_category, detail)
