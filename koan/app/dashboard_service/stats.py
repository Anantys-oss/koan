"""Forecast, skill metrics, and agent-state readers (no Flask)."""
import sys

from app.dashboard import state

_EMPTY_FORECAST = {
    "burn_rate_pct_per_minute": None,
    "time_to_exhaustion_minutes": None,
    "session_pct": None,
    "autonomous_mode": None,
    "samples_count": 0,
    "status": "warming_up",
}


def get_signal_status() -> dict:
    """Read .koan-* signal files. Delegates to ``agent_state`` module."""
    from app.agent_state import get_signal_status as _get_signal_status

    return _get_signal_status(state.KOAN_ROOT)


def get_agent_state() -> dict:
    """Derive a structured agent state from signal files.

    Delegates to ``agent_state`` module.
    """
    from app.agent_state import get_agent_state as _get_agent_state

    return _get_agent_state(state.KOAN_ROOT)


def build_forecast() -> dict:
    """Assemble burn-rate and session-usage data into a forecast dict.

    Returns a dict with keys: burn_rate_pct_per_minute, time_to_exhaustion_minutes,
    session_pct, autonomous_mode, samples_count, status.
    Status is one of 'normal', 'warming_up', 'paused'.
    """
    try:
        from app.burn_rate import BurnRateSnapshot, MIN_SAMPLES_FOR_ESTIMATE
        from app.iteration_manager import _read_session_pct_and_reset
    except ImportError as exc:
        print(f"[dashboard] forecast import error: {exc}", file=sys.stderr)
        return {**_EMPTY_FORECAST}

    signals = get_signal_status()
    if signals.get("paused") or signals.get("quota_paused"):
        return {**_EMPTY_FORECAST, "status": "paused"}

    snapshot = BurnRateSnapshot(state.INSTANCE_DIR)
    samples_count = len(snapshot.samples)
    rate = snapshot.burn_rate_pct_per_minute()

    if samples_count < MIN_SAMPLES_FOR_ESTIMATE or rate is None:
        return {**_EMPTY_FORECAST, "samples_count": samples_count}

    usage_state_path = state.INSTANCE_DIR / "usage_state.json"
    session_pct, _, _ = _read_session_pct_and_reset(usage_state_path)
    if session_pct is None:
        return {
            "burn_rate_pct_per_minute": rate,
            "time_to_exhaustion_minutes": None,
            "session_pct": None,
            "autonomous_mode": None,
            "samples_count": samples_count,
            "status": "warming_up",
        }

    agent_state = get_agent_state()
    autonomous_mode = agent_state.get("autonomous_mode") or None
    mode_key = autonomous_mode.lower() if autonomous_mode else None
    tte = snapshot.time_to_exhaustion(session_pct, mode=mode_key)

    return {
        "burn_rate_pct_per_minute": rate,
        "time_to_exhaustion_minutes": tte,
        "session_pct": session_pct,
        "autonomous_mode": autonomous_mode,
        "samples_count": samples_count,
        "status": "normal",
    }


def compute_dashboard_skill_metrics(selected_project: str = "") -> dict:
    """Compute skill metrics summaries for dashboard display.

    Returns dict mapping project names to their summary dicts.
    If selected_project is set, only returns that project.
    """
    from app.skill_metrics import compute_summary

    projects_dir = state.INSTANCE_DIR / "memory" / "projects"
    if not projects_dir.exists():
        return {}

    result = {}
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        pname = project_dir.name
        if selected_project and pname != selected_project:
            continue
        metrics_file = project_dir / "skill-metrics.md"
        if not metrics_file.exists():
            continue
        summary = compute_summary(str(state.INSTANCE_DIR), pname, days=30)
        if summary["plan_total"] > 0 or summary["pr_total"] > 0:
            result[pname] = summary
    return result
