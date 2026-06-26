"""Usage blueprint: usage/metrics/efficiency/skill-metrics + journal + logs."""
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from app.dashboard import state
from app.dashboard_service import journal as journal_svc
from app.dashboard_service import stats as stats_svc
from app.log_reader import LOG_DEFAULT_LIMIT, read_logs
from app.usage_service import build_usage_payload

usage_bp = Blueprint("usage", __name__)


@usage_bp.route("/usage")
def usage_page():
    """Usage tracking page — per-project and per-model token breakdown."""
    return render_template("usage.html")


@usage_bp.route("/api/usage")
def api_usage():
    """JSON usage data for the specified time range."""
    try:
        days = int(request.args.get("days", "7"))
    except (ValueError, TypeError):
        days = 7
    try:
        offset = int(request.args.get("offset", "0"))
    except (ValueError, TypeError):
        offset = 0
    stacked = request.args.get("stacked", "false").lower() in ("true", "1", "yes")
    return jsonify(build_usage_payload(
        state.INSTANCE_DIR,
        days=days,
        project=request.args.get("project", ""),
        granularity=request.args.get("granularity", "day"),
        stacked=stacked,
        offset=offset,
    ))


@usage_bp.route("/api/usage/missions")
def api_usage_missions():
    """Per-mission cost drill-down, sorted by total tokens descending."""
    from app.cost_tracker import top_missions

    days = request.args.get("days", "7", type=str)
    selected_project = request.args.get("project", "")
    offset_raw = request.args.get("offset", "0", type=str)
    limit_raw = request.args.get("limit", "100", type=str)

    try:
        days = max(1, min(int(days), 100))
    except (ValueError, TypeError):
        days = 7

    try:
        offset = max(0, int(offset_raw))
    except (ValueError, TypeError):
        offset = 0

    try:
        limit = max(1, min(int(limit_raw), 200))
    except (ValueError, TypeError):
        limit = 100

    today = date.today()
    end = today - timedelta(days=offset * days)
    start = end - timedelta(days=days - 1)

    missions = top_missions(
        state.INSTANCE_DIR,
        start,
        end,
        project=selected_project or None,
        limit=limit,
    )
    return jsonify({"missions": missions, "start": start.isoformat(), "end": end.isoformat()})


@usage_bp.route("/api/metrics")
def api_metrics():
    """JSON mission metrics for the specified time range."""
    from app.mission_metrics import (
        compute_global_metrics,
        compute_project_metrics,
        compute_project_trend,
    )

    days = request.args.get("days", "30", type=str)
    selected_project = request.args.get("project", "")
    try:
        days = int(days)
        days = max(0, min(days, 365))
    except (ValueError, TypeError):
        days = 30

    if selected_project:
        metrics = compute_project_metrics(str(state.INSTANCE_DIR), selected_project, days=days)
        metrics["trend"] = compute_project_trend(str(state.INSTANCE_DIR), selected_project, days=days)
        return jsonify(metrics)

    # Global metrics with per-project trends
    metrics = compute_global_metrics(str(state.INSTANCE_DIR), days=days)
    for proj in metrics["by_project"]:
        metrics["by_project"][proj]["trend"] = compute_project_trend(
            str(state.INSTANCE_DIR), proj, days=days
        )
    return jsonify(metrics)


@usage_bp.route("/api/efficiency")
def api_efficiency():
    """Per-project token efficiency: cost per productive outcome."""
    import calendar as _calendar

    from app.cost_tracker import summarize_range
    from app.session_tracker import load_outcomes

    days = request.args.get("days", "30", type=str)
    selected_project = request.args.get("project", "")
    offset_raw = request.args.get("offset", "0", type=str)
    granularity = request.args.get("granularity", "day")
    if granularity not in ("day", "week", "month"):
        granularity = "day"
    try:
        days = int(days)
        days = max(1, min(days, 365))
    except (ValueError, TypeError):
        days = 30
    try:
        offset = int(offset_raw)
        offset = max(0, offset)
    except (ValueError, TypeError):
        offset = 0

    today = date.today()
    if granularity == "week":
        end = today - timedelta(weeks=offset)
        start = end - timedelta(days=days - 1)
    elif granularity == "month":
        year, month = today.year, today.month
        month -= offset
        while month <= 0:
            month += 12
            year -= 1
        last_day = _calendar.monthrange(year, month)[1]
        end = date(year, month, min(today.day, last_day))
        start = end - timedelta(days=days - 1)
    else:
        end = today - timedelta(days=offset * days)
        start = end - timedelta(days=days - 1)

    # --- Outcome counts by project ---
    outcomes_path = state.INSTANCE_DIR / "session_outcomes.json"
    all_outcomes = load_outcomes(outcomes_path)
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

    outcome_counts: dict[str, dict[str, int]] = {}
    for o in all_outcomes:
        ts = o.get("timestamp", "")
        try:
            ts_dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if ts_dt < start_dt or ts_dt >= end_dt:
            continue
        proj = o.get("project", "")
        if not proj or (selected_project and proj != selected_project):
            continue
        if proj not in outcome_counts:
            outcome_counts[proj] = {"productive": 0, "empty": 0, "blocked": 0}
        outcome = o.get("outcome", "")
        if outcome in outcome_counts[proj]:
            outcome_counts[proj][outcome] += 1

    # --- Token totals by project ---
    summary = summarize_range(state.INSTANCE_DIR, start, end)
    cost_by_project = summary.get("by_project", {})
    if selected_project:
        cost_by_project = {k: v for k, v in cost_by_project.items() if k == selected_project}

    # --- Join ---
    all_projects = set(outcome_counts.keys()) | set(cost_by_project.keys())
    by_project: dict[str, dict] = {}
    for proj in sorted(all_projects):
        oc = outcome_counts.get(proj, {"productive": 0, "empty": 0, "blocked": 0})
        cost = cost_by_project.get(proj, {})
        total_tokens = cost.get("input_tokens", 0) + cost.get("output_tokens", 0)
        productive = oc["productive"]
        empty = oc["empty"]
        blocked = oc["blocked"]
        total_sessions = productive + empty + blocked

        tppo = total_tokens / productive if productive > 0 else None
        waste = (empty + blocked) / total_sessions if total_sessions > 0 else (1.0 if total_tokens > 0 else 0.0)

        by_project[proj] = {
            "productive_count": productive,
            "empty_count": empty,
            "blocked_count": blocked,
            "total_sessions": total_sessions,
            "total_tokens": total_tokens,
            "tokens_per_productive_outcome": tppo,
            "waste_pct": round(waste, 4),
        }

    return jsonify({"by_project": by_project, "days": days})


@usage_bp.route("/api/skill-metrics")
def api_skill_metrics():
    """JSON skill metrics (plan approval + CI pass rates) per project."""
    selected_project = request.args.get("project", "")
    return jsonify(stats_svc.compute_dashboard_skill_metrics(selected_project))


@usage_bp.route("/journal")
def journal_page():
    """Journal viewer — shows today by default, with day selector for last 7 days."""
    dates = journal_svc.get_journal_dates(limit=7)
    selected_date = request.args.get("date", "")
    if selected_date and selected_date not in dates:
        selected_date = ""
    if not selected_date and dates:
        selected_date = dates[0]
    selected_project = request.args.get("project", "")
    entries = journal_svc.get_journal_day(selected_date) if selected_date else []
    if selected_project:
        entries = [e for e in entries if e["project"] == selected_project]
    return render_template(
        "journal.html",
        dates=dates,
        selected_date=selected_date,
        entries=entries,
        selected_project=selected_project,
    )


@usage_bp.route("/api/journal/<day>")
def api_journal_day(day):
    """Return journal entries for a single date (on-demand loading)."""
    if not re.match(r"\d{4}-\d{2}-\d{2}$", day):
        return jsonify({"error": "invalid date format"}), 400
    project = request.args.get("project", "")
    entries = journal_svc.get_journal_day(day)
    if project:
        entries = [e for e in entries if e["project"] == project]
    return jsonify({"date": day, "entries": entries})


@usage_bp.route("/api/logs")
def api_logs():
    """Return recent log lines from run.log and/or awake.log.

    Query params:
      source  — "run", "awake", or "all" (default "all")
      limit   — max lines to return per source (default 200, max 2000)
      q       — optional substring filter (case-insensitive)
    """
    source = request.args.get("source", "all")
    try:
        limit = int(request.args.get("limit", LOG_DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = LOG_DEFAULT_LIMIT
    q = request.args.get("q", "")
    return jsonify(read_logs(state.KOAN_ROOT, source=source, limit=limit, q=q))


@usage_bp.route("/logs")
def logs_page():
    """Log viewer page."""
    return render_template("logs.html")
