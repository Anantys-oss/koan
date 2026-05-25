"""
Kōan -- Iteration planning for the main run loop.

Consolidates per-iteration decision-making into a single Python call:
1. Refresh usage from accumulated token state
2. Decide autonomous mode (wait/review/implement/deep)
3. Inject due recurring missions
4. Pick next mission (or enter autonomous mode)
5. Resolve project path from mission or round-robin
6. Handle autonomous mode decisions (contemplative, focus, WAIT)
7. Resolve focus area description

CLI interface:
    python -m app.iteration_manager plan-iteration \\
        --instance <dir> --koan-root <dir> \\
        --run-num <int> --count <int> \\
        --projects <semicolon-separated> \\
        --last-project <name> \\
        --usage-state <path>

Output: JSON on stdout with iteration plan.
"""

import argparse
import json
import random
import re
import sys
from collections import namedtuple
from pathlib import Path
from typing import List, Optional, Tuple

from app.constants import (
    BURN_RATE_DOWNGRADE_THRESHOLD_MIN,
    BURN_RATE_WARNING_MIN_RESET_GAP_MIN,
    BURN_RATE_WARNING_THRESHOLD_MIN,
    MAX_SELECTION_AUDIT_ENTRIES as _MAX_SELECTION_AUDIT_ENTRIES,
)
from app.loop_manager import resolve_focus_area
from app.run_log import log_safe, suppress_logged


# Set to True when running as CLI subprocess (stdout carries JSON).
_cli_mode = False


def _log_iteration(category: str, message: str):
    """Log iteration events, routing to stderr in CLI subprocess mode."""
    log_safe(category, message, force_stderr=_cli_mode)


def _refresh_usage(usage_state: Path, usage_md: Path, count: int):
    """Refresh usage.md from accumulated token state.

    Always refreshes — critical after auto-resume so stale usage.md
    is cleared and session resets are detected.
    """
    try:
        from app.usage_estimator import cmd_refresh
        cmd_refresh(usage_state, usage_md)
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Usage refresh error: {e}")


_MODE_DOWNGRADE = {
    "deep": "implement",
    "implement": "review",
    "review": "wait",
}

def _downgrade_if_unaffordable(tracker, mode: str,
                               tier_multiplier: float = 1.0) -> str:
    """Downgrade mode until can_afford_run() passes or we hit wait.

    Called after decide_mode() to ensure the estimated run cost
    actually fits within remaining budget. Prevents launching a deep
    session when budget can only cover a review.

    Args:
        tracker: UsageTracker instance.
        mode: Current autonomous mode.
        tier_multiplier: Additional cost multiplier from complexity tier
            (e.g. 1.5 for complex missions). Applied on top of the mode
            multiplier so tier-based model upgrades don't silently bypass
            the budget guard.
    """
    original = mode
    while mode in _MODE_DOWNGRADE and not tracker.can_afford_run(
        mode, tier_multiplier=tier_multiplier,
    ):
        mode = _MODE_DOWNGRADE[mode]
    if mode != original:
        tier_info = f", tier_mult={tier_multiplier:.1f}" if tier_multiplier != 1.0 else ""
        _log_iteration("koan",
            f"Budget check: downgraded {original} → {mode} "
            f"(estimated cost {tracker.estimate_run_cost():.1f}%{tier_info})")
    return mode


def _downgrade_if_burning_fast(instance_dir: Path, session_pct: float,
                               mode: str):
    """Drop one tier when projected exhaustion is imminent.

    Returns (mode, downgraded_from) where downgraded_from is the previous
    mode if a downgrade fired, else None.
    """
    if mode == "wait" or mode not in _MODE_DOWNGRADE:
        return mode, None
    try:
        from app.burn_rate import BurnRateSnapshot
        snapshot = BurnRateSnapshot(instance_dir)
        tte = snapshot.time_to_exhaustion(session_pct, mode=mode)
    except (ImportError, OSError, ValueError):
        return mode, None
    if tte is None or tte >= BURN_RATE_DOWNGRADE_THRESHOLD_MIN:
        return mode, None
    downgraded = _MODE_DOWNGRADE.get(mode, mode)
    if downgraded == mode:
        return mode, None
    _log_iteration("koan",
        f"Burn-rate downgrade: {mode} → {downgraded} "
        f"(est. {tte:.0f} min to exhaustion)")
    return downgraded, mode


def _get_usage_decision(usage_md: Path, count: int, projects_str: str):
    """Parse usage.md and decide autonomous mode.

    Returns:
        dict with keys: mode, available_pct, reason, display_lines
    """
    try:
        from app.usage_tracker import UsageTracker, _get_budget_mode, _get_budget_thresholds
        budget_mode = _get_budget_mode()
        warn_pct, stop_pct = _get_budget_thresholds()
        tracker = UsageTracker(usage_md, count, budget_mode=budget_mode,
                               warn_pct=warn_pct, stop_pct=stop_pct)
        mode = tracker.decide_mode()

        # Burn-rate downgrade: applied here (not inside UsageTracker) so the
        # tracker stays a pure parser+threshold class with no I/O coupling.
        mode, burn_downgrade_from = _downgrade_if_burning_fast(
            usage_md.parent, tracker.session_pct, mode,
        )

        # Verify the chosen mode is affordable; downgrade if not
        mode = _downgrade_if_unaffordable(tracker, mode)

        session_rem, weekly_rem = tracker.remaining_budget()
        available_pct = int(min(session_rem, weekly_rem))
        reason = tracker.get_decision_reason(mode)
        if burn_downgrade_from:
            reason += f" (burn-rate downgrade from {burn_downgrade_from})"

        # Get display lines for console output
        display_lines = []
        if usage_md.exists():
            content = usage_md.read_text()
            session_match = re.search(r'^.*Session.*$', content, re.MULTILINE | re.IGNORECASE)
            weekly_match = re.search(r'^.*Weekly.*$', content, re.MULTILINE | re.IGNORECASE)
            if session_match:
                display_lines.append(session_match.group(0).strip())
            if weekly_match:
                display_lines.append(weekly_match.group(0).strip())

        # Get today's actual cost from cost tracker (accurate, not estimated)
        cost_today = _get_cost_today(usage_md.parent)

        return {
            "mode": mode,
            "available_pct": available_pct,
            "reason": reason,
            "display_lines": display_lines,
            "cost_today": cost_today,
            "tracker": tracker,
        }
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Usage tracker error: {e}")
        return {
            "mode": "review",
            "available_pct": 0,
            "reason": "Tracker error — safe fallback (review only)",
            "display_lines": [],
            "tracker_error": str(e),
        }


def _read_session_pct_and_reset(usage_state_path: Path):
    """Return (session_pct, minutes_until_session_reset) or (None, None).

    Reads usage_state.json directly so the warning logic does not depend on
    the freshness of usage.md.
    """
    try:
        import json
        from datetime import datetime
        from app.usage_estimator import (
            SESSION_DURATION_HOURS,
            _get_limits,
        )
        from app.utils import load_config
    except (ImportError, OSError, ValueError):
        return None, None

    if not usage_state_path.exists():
        return None, None

    try:
        state = json.loads(usage_state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None

    try:
        session_limit, _ = _get_limits(load_config())
    except (OSError, ValueError, TypeError):
        return None, None
    if session_limit <= 0:
        return None, None

    tokens = state.get("session_tokens", 0) or 0
    session_pct = min(100.0, tokens / session_limit * 100.0)

    try:
        session_start = datetime.fromisoformat(state["session_start"])
    except (KeyError, ValueError, TypeError):
        return session_pct, None

    elapsed = (datetime.now() - session_start).total_seconds() / 60.0
    minutes_remaining = max(0.0, SESSION_DURATION_HOURS * 60.0 - elapsed)
    return session_pct, minutes_remaining


def _maybe_warn_burn_rate(instance_dir: Path, usage_state_path: Path) -> None:
    """Fire a Telegram warning when projected exhaustion is imminent.

    Conditions (all must hold):
      - rolling burn rate has enough history to estimate
      - time-to-exhaustion < 60 minutes
      - session reset is still > 2 hours away (otherwise quota will reset
        before the user could meaningfully react)
      - no warning has been fired since the start of the current session
    """
    try:
        from app.burn_rate import (
            BurnRateSnapshot,
            mark_warned,
            clear_warning,
        )
    except ImportError:
        return

    session_pct, minutes_until_reset = _read_session_pct_and_reset(
        usage_state_path
    )
    if session_pct is None or minutes_until_reset is None:
        return

    # Single load for all read operations (was 4 separate file reads).
    snapshot = BurnRateSnapshot(instance_dir)

    last_warned = snapshot.last_warned_at
    if last_warned is not None:
        with suppress_logged(_log_iteration, "error", "Burn rate warning state parse failed",
                             json.JSONDecodeError, OSError, KeyError, ValueError, TypeError):
            import json
            from datetime import datetime, timezone
            state = json.loads(usage_state_path.read_text())
            session_start = datetime.fromisoformat(state["session_start"])
            if session_start.tzinfo is None:
                session_start = session_start.replace(tzinfo=timezone.utc)
            if last_warned < session_start:
                clear_warning(instance_dir)
                last_warned = None

    if last_warned is not None:
        return  # Already warned for this session cycle

    if minutes_until_reset <= BURN_RATE_WARNING_MIN_RESET_GAP_MIN:
        return  # Quota will reset soon anyway — no point alerting

    tte = snapshot.time_to_exhaustion(session_pct)
    if tte is None or tte >= BURN_RATE_WARNING_THRESHOLD_MIN:
        return

    rate = snapshot.burn_rate_pct_per_minute() or 0.0
    msg = (
        "⚠️ Burn-rate alert: at "
        f"{rate * 60:.1f}%/h the session quota will be exhausted in "
        f"~{tte:.0f} min, but resets in "
        f"~{minutes_until_reset / 60:.1f}h. Consider pausing or switching to "
        "lighter missions."
    )

    try:
        from app.utils import append_to_outbox
        outbox = Path(instance_dir) / "outbox.md"
        append_to_outbox(outbox, msg)
    except (ImportError, OSError) as exc:
        _log_iteration("error", f"Burn-rate warning send failed: {exc}")
        return

    mark_warned(instance_dir)


def _get_cost_today(instance_dir: Path) -> float:
    """Get today's actual API cost from cost tracker JSONL data.

    Returns 0.0 if cost tracking is unavailable.
    """
    try:
        from app.cost_tracker import summarize_day
        summary = summarize_day(instance_dir)
        return summary.get("total_cost_usd", 0.0)
    except (ImportError, OSError, ValueError, KeyError) as e:
        _log_iteration("error", f"Cost tracker read failed: {e}")
        return 0.0


def _inject_recurring(instance_dir: Path):
    """Inject due recurring missions into the pending queue.

    Returns:
        list of injection descriptions (for logging)
    """
    recurring_path = instance_dir / "recurring.json"
    if not recurring_path.exists():
        return []

    try:
        from app.recurring import check_and_inject
        missions_path = instance_dir / "missions.md"
        return check_and_inject(recurring_path, missions_path)
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Recurring injection error: {e}")
        return []


def _drain_ci_queue(instance_dir: Path):
    """Drain one CI queue entry (non-blocking).

    Returns:
        status message string, or None if queue is empty / still pending.
    """
    try:
        from app.ci_queue_runner import drain_one
        return drain_one(str(instance_dir))
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"CI queue drain error: {e}")
        return None


def _dispatch_ci_fixes(instance_dir: Path, koan_root: str):
    """Auto-dispatch fix missions for failing CI on Koan PRs."""
    try:
        from app.ci_dispatch import check_and_dispatch_ci_fixes
        count = check_and_dispatch_ci_fixes(str(instance_dir), koan_root)
        if count > 0:
            _log_iteration("koan", f"CI dispatch: {count} fix mission(s) queued")
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"CI dispatch error: {e}")


def _fallback_mission_extract(instance_dir: Path, projects_str: str,
                              context_msg: str):
    """Attempt direct mission extraction when the picker fails or returns empty.

    Safety net that bypasses the Claude-based picker and reads missions.md
    directly.  Shared by both the "picker returned nothing" and "picker
    crashed" branches inside ``_pick_mission()``.

    Returns:
        (project_name, mission_title) or (None, None)
    """
    try:
        from app.missions import count_pending
        from app.pick_mission import fallback_extract

        missions_path = instance_dir / "missions.md"
        try:
            content = missions_path.read_text()
        except FileNotFoundError:
            return None, None

        pending_count = count_pending(content)
        if pending_count <= 0:
            return None, None

        _log_iteration("error",
            f"{context_msg} — {pending_count} pending mission(s) exist "
            f"— attempting direct extraction")
        project, title = fallback_extract(content, projects_str)
        if project and title:
            _log_iteration("mission",
                f"Direct fallback picked: [{project}] {title[:60]}")
            return project, title

        _log_iteration("error", "Direct fallback also failed to extract a mission")
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Fallback mission extract failed: {e}")
    return None, None


def _pick_mission(instance_dir: Path, projects_str: str, run_num: int,
                  autonomous_mode: str, last_project: str):
    """Pick next mission from the queue.

    Returns:
        (project_name, mission_title) or (None, None) for autonomous mode
    """
    try:
        from app.pick_mission import pick_mission
        result = pick_mission(
            str(instance_dir), projects_str,
            str(run_num), autonomous_mode, last_project,
        )
        if result:
            parts = result.split(":", 1)
            if len(parts) == 2:
                return parts[0], parts[1]
        # pick_mission returned empty — safety net for silent picker failures
        return _fallback_mission_extract(
            instance_dir, projects_str,
            "Mission picker returned nothing but")
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Mission picker error: {e}")
        return _fallback_mission_extract(
            instance_dir, projects_str,
            "Picker crashed but")


def _classify_mission(
    mission_title: str,
    project_name: str,
    missions_path,
) -> Optional[str]:
    """Classify mission complexity and cache the tier in missions.md.

    Checks for an existing [complexity:X] tag first (cache hit).  If
    absent, calls the lightweight model to classify the mission.

    Args:
        mission_title: The mission text to classify.
        project_name: Project name for per-project model/routing config.
        missions_path: Path to missions.md for tag caching.

    Returns:
        Tier string ("trivial", "simple", "medium", "complex") or None
        when routing is disabled or classification fails.
    """
    try:
        from app.config import get_complexity_routing_config
        routing = get_complexity_routing_config(project_name)
        if routing is None:
            return None  # Routing disabled for this project
    except Exception as e:
        _log_iteration("error", f"Complexity routing config error: {e}")
        return None

    # Cache hit — already classified
    try:
        from app.missions import extract_complexity_tag
        cached = extract_complexity_tag(mission_title)
        if cached is not None:
            _log_iteration("complexity",
                f"mission='{mission_title[:60]}' tier={cached} (cached)")
            return cached
    except Exception as e:
        _log_iteration("error", f"Complexity tag extraction error: {e}")

    # Cache miss — call the classifier
    try:
        from app.complexity_classifier import classify_mission_complexity
        tier_obj = classify_mission_complexity(mission_title, project_name)
        tier = tier_obj.value
    except Exception as e:
        _log_iteration("error", f"Complexity classification error: {e}")
        return "medium"  # Safe default

    _log_iteration("complexity",
        f"mission='{mission_title[:60]}' tier={tier}")

    # Write tag to missions.md (best-effort — never block execution)
    try:
        from app.missions import tag_complexity_in_pending
        tag_complexity_in_pending(mission_title, tier, missions_path)
    except Exception as e:
        _log_iteration("error", f"Complexity tag write error: {e}")

    return tier


def _get_tier_cost_multiplier(tier: Optional[str],
                              project_name: str = "") -> float:
    """Look up the cost multiplier for a complexity tier.

    Uses ``timeout_multiplier`` from the complexity routing config as a
    proxy for cost — longer timeouts and more turns correlate with higher
    token spend.  Falls back to 1.0 when routing is disabled or the tier
    has no explicit multiplier.
    """
    if not tier:
        return 1.0
    try:
        from app.config import get_complexity_routing_config
        routing = get_complexity_routing_config(project_name)
        if routing is None:
            return 1.0
        tier_cfg = routing.get("tiers", {}).get(tier, {})
        return float(tier_cfg.get("timeout_multiplier", 1.0))
    except (ImportError, OSError, ValueError, TypeError):
        return 1.0


def _projects_to_str(projects: List[Tuple[str, str]]) -> str:
    """Convert a list of (name, path) tuples to semicolon-separated string.

    This is used for downstream functions that still expect the string format
    (pick_mission).
    """
    return ";".join(f"{name}:{path}" for name, path in projects)


def _resolve_project_path(
    project_name: str, projects: List[Tuple[str, str]],
) -> Optional[Tuple[str, str]]:
    """Find the canonical name and path for a project name (case-insensitive).

    Returns:
        (canonical_name, path) tuple or None if not found
    """
    lower = project_name.lower()
    for name, path in projects:
        if name.lower() == lower:
            return (name, path)
    return None


def _get_known_project_names(projects: List[Tuple[str, str]]) -> list:
    """Extract sorted list of project names."""
    return sorted(name for name, _ in projects)


def _should_contemplate(autonomous_mode: str, focus_active: bool,
                        contemplative_chance: int,
                        schedule_state=None,
                        focus_mode: bool = False) -> bool:
    """Check if this iteration should be a contemplative session.

    Contemplative sessions only trigger when:
    - Focus mode is NOT active (neither config-level nor file-based)
    - Mode is deep or implement (need budget for Claude call)
    - Schedule is not in work_hours
    - Random roll succeeds (chance boosted during deep_hours)

    Returns:
        True if should run a contemplative session
    """
    if focus_mode:
        return False

    if autonomous_mode not in ("deep", "implement"):
        return False

    if focus_active:
        return False

    # Adjust chance based on schedule (work hours → 0, deep hours → 3x)
    if schedule_state is not None:
        from app.schedule_manager import adjust_contemplative_chance
        contemplative_chance = adjust_contemplative_chance(
            contemplative_chance, schedule_state
        )

    return random.randint(0, 99) < contemplative_chance


def _check_focus(koan_root: str):
    """Check focus mode state.

    Returns:
        Focus state object if active, None if not active.
        Gracefully returns None if focus_manager module is not available.
    """
    try:
        from app.focus_manager import check_focus
        return check_focus(koan_root)
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Focus check failed: {e}")
        return None


def _check_passive(koan_root: str):
    """Check passive mode state.

    Returns:
        PassiveState object if active, None if not active.
        Gracefully returns None if passive_manager module is not available.
    """
    try:
        from app.passive_manager import check_passive
        return check_passive(koan_root)
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Passive check failed: {e}")
        return None


def _log_selection_audit(
    instance_dir: str,
    candidates: List[Tuple[str, str]],
    candidate_weights: List[float],
    freshness: Optional[dict],
    drift: Optional[dict],
    success_rates: Optional[dict],
    ts_samples: dict,
    combined: list,
    selected: str,
) -> None:
    """Append a structured entry to .selection-audit.json for debugging.

    Captures all signals that contributed to the project selection decision
    so post-hoc analysis can identify selection biases or misconfigured weights.
    Ring-buffered to _MAX_SELECTION_AUDIT_ENTRIES entries.
    """
    from datetime import datetime

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "selected": selected,
        "candidates": {},
    }
    for i, (name, _) in enumerate(candidates):
        entry["candidates"][name] = {
            "weight": candidate_weights[i] if i < len(candidate_weights) else None,
            "freshness": freshness.get(name) if freshness else None,
            "drift": drift.get(name, 0) if drift else None,
            "success_rate": (
                round(success_rates.get(name, 0.5), 3)
                if success_rates else None
            ),
            "ts_sample": (
                round(ts_samples[name], 4)
                if name in ts_samples else None
            ),
            "combined": round(combined[i], 4) if i < len(combined) else None,
        }

    audit_path = Path(instance_dir) / ".selection-audit.json"
    try:
        from app.utils import atomic_write
        existing = []
        if audit_path.exists():
            raw = json.loads(audit_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        existing.append(entry)
        if len(existing) > _MAX_SELECTION_AUDIT_ENTRIES:
            existing = existing[-_MAX_SELECTION_AUDIT_ENTRIES:]
        atomic_write(audit_path, json.dumps(existing, indent=2))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        _log_iteration("error", f"Selection audit write failed: {e}")


def _select_random_exploration_project(
    projects: List[Tuple[str, str]],
    last_project: str = "",
    instance_dir: str = "",
) -> Tuple[str, str]:
    """Randomly select a project for autonomous exploration.

    Uses session outcome history to weight selection: fresh projects
    (recently productive) are preferred over stale ones (consecutive
    empty sessions). By default, avoids repeating the last explored
    project, but can optionally stay on the same project to preserve
    prompt-cache warmth across consecutive runs.

    Args:
        projects: List of eligible (name, path) tuples (must be non-empty).
        last_project: Name of the project used in the previous iteration.
        instance_dir: Path to instance directory (for freshness lookup).

    Returns:
        (name, path) tuple of the selected project.
    """
    if len(projects) == 1:
        return projects[0]

    # Optional cache-aware "fast lane": intentionally keep the same project
    # as the previous run to maximize prompt prefix cache reuse.
    if last_project and len(projects) > 1:
        previous = next(((n, p) for n, p in projects if n == last_project), None)
        if previous:
            try:
                from app.config import get_same_project_stickiness_percent

                stickiness = get_same_project_stickiness_percent()
            except (ImportError, OSError, ValueError) as e:
                _log_iteration("error", f"Stickiness config lookup failed: {e}")
                stickiness = 0

            if stickiness > 0:
                roll = random.randint(1, 100)
                if roll <= stickiness:
                    _log_iteration(
                        "koan",
                        f"Cache fast lane: reusing project '{last_project}' "
                        f"(roll={roll} <= stickiness={stickiness})",
                    )
                    return previous

    # Load session outcomes once for both freshness and drift lookups
    # (avoids 2N file reads — one per project per function)
    weights = None
    drift = None
    success_rates = None
    if instance_dir:
        try:
            from app.session_tracker import (
                load_outcomes, get_project_freshness, get_project_drift,
            )
            from pathlib import Path as _Path
            outcomes_path = _Path(instance_dir) / "session_outcomes.json"
            all_outcomes = load_outcomes(outcomes_path)

            weights = get_project_freshness(instance_dir, projects,
                                             _all_outcomes=all_outcomes)
            drift = get_project_drift(instance_dir, projects,
                                       _all_outcomes=all_outcomes)
        except (ImportError, OSError, ValueError) as e:
            _log_iteration("error", f"Freshness/drift lookup failed: {e}")

        try:
            from app.mission_metrics import get_project_success_rates
            project_names = [n for n, _ in projects]
            success_rates = get_project_success_rates(
                instance_dir, project_names, days=30,
                _all_outcomes=all_outcomes,
            )
        except (ImportError, OSError, ValueError) as e:
            _log_iteration("error", f"Success rate lookup failed: {e}")

    # Filter out last project when possible
    candidates = projects
    if last_project and len(projects) > 1:
        filtered = [(n, p) for n, p in projects if n != last_project]
        if filtered:
            candidates = filtered

    # Weighted random selection combining freshness and drift.
    # NOTE: success_rate is NOT included in the weight computation because
    # the Thompson Sampling bandit already encodes productive/non-productive
    # outcomes via its Beta distribution.  Adding success_rate here would
    # double-count the signal (bandit alpha/beta AND explicit weight bonus).
    # Success rate is still logged for observability.
    if (weights or drift) and len(candidates) > 1:
        candidate_weights = []
        for name, _ in candidates:
            base = weights.get(name, 10) if weights else 10
            # Drift boost: projects with significant new commits get a bonus
            if drift:
                d = drift.get(name, 0)
                if d >= 15:
                    base += 6  # High drift — strong pull
                elif d >= 5:
                    base += 3  # Moderate drift
                elif d >= 3:
                    base += 1  # Minor drift
            candidate_weights.append(base)

        total = sum(candidate_weights)
        if total > 0:
            # Thompson Sampling: each candidate gets a Beta sample scaled
            # by the staleness/drift score.  The existing score acts as a
            # context multiplier (signals the bandit cannot observe),
            # while the bandit handles exploitation vs exploration.
            # argmax over combined scores replaces random.choices().
            try:
                from app.bandit import load_bandit_state, thompson_sample
                bandit = load_bandit_state(instance_dir)
                ts_samples = {}
                combined = []
                for (name, _), w in zip(candidates, candidate_weights, strict=True):
                    sample = thompson_sample(bandit, name)
                    ts_samples[name] = sample
                    combined.append(w * sample)
                best_idx = combined.index(max(combined))
                selected = candidates[best_idx]
            except Exception as e:
                # Fallback to weighted random on any bandit error
                print(f"[iteration] bandit sampling error: {e}", file=sys.stderr)
                selected = random.choices(candidates, weights=candidate_weights, k=1)[0]
                ts_samples = {}
                combined = []

            # Audit trail: log all signals for every candidate so selection
            # decisions can be debugged after the fact.
            _log_selection_audit(
                instance_dir, candidates, candidate_weights,
                weights, drift, success_rates, ts_samples, combined,
                selected[0],
            )

            extra_info = []
            if weights:
                staleness = 10 - weights.get(selected[0], 10)
                if staleness > 0:
                    extra_info.append(f"staleness={staleness}")
            if drift:
                d = drift.get(selected[0], 0)
                if d > 0:
                    extra_info.append(f"drift={d} commits")
            if success_rates:
                rate = success_rates.get(selected[0], 0.5)
                if rate != 0.5:
                    extra_info.append(f"success={rate:.0%}")
            if ts_samples:
                extra_info.append(f"ts={ts_samples.get(selected[0], 0):.3f}")
            suffix = f" ({', '.join(extra_info)})" if extra_info else ""
            _log_iteration("koan",
                f"Thompson Sampling: '{selected[0]}'{suffix} "
                f"from {len(candidates)} candidate(s)")
            return selected

    return random.choice(candidates)


_DIAGNOSTIC_COOLDOWN_FILE = ".diagnostic-cooldowns.json"

# Mode hierarchy for min_mode gating (higher index = more permissive)
_MODE_RANK = {"wait": 0, "review": 1, "implement": 2, "deep": 3}


def _select_diagnostic_type(
    instance_dir: str,
    project_name: str,
) -> str:
    """Choose which diagnostic skill to run for a sick project.

    Selection logic:
      - "declining" trend → tech_debt (structural issues causing failures)
      - Majority "empty" outcomes → dead_code (cleanup to unblock exploration)
      - Otherwise (blocked/stagnated) → audit (deeper investigation)
    """
    with suppress_logged(_log_iteration, "error", "Diagnostic type detection failed",
                         ImportError, OSError, ValueError):
        from app.mission_metrics import compute_project_metrics, compute_project_trend

        trend = compute_project_trend(instance_dir, project_name, days=30)
        if trend == "declining":
            return "tech_debt"

        metrics = compute_project_metrics(instance_dir, project_name, days=30)
        total = metrics.get("total_sessions", 0)
        empty = metrics.get("empty", 0)
        if total > 0 and empty / total > 0.5:
            return "dead_code"

    return "audit"


def _load_diagnostic_cooldowns(instance_dir: str) -> dict:
    """Load per-project diagnostic cooldown timestamps."""
    cooldown_path = Path(instance_dir) / _DIAGNOSTIC_COOLDOWN_FILE
    if not cooldown_path.exists():
        return {}
    try:
        return json.loads(cooldown_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_diagnostic_cooldown(instance_dir: str, project_name: str):
    """Record that a diagnostic mission was injected for a project."""
    from datetime import datetime

    cooldowns = _load_diagnostic_cooldowns(instance_dir)
    cooldowns[project_name] = datetime.now().isoformat()

    cooldown_path = Path(instance_dir) / _DIAGNOSTIC_COOLDOWN_FILE
    try:
        from app.utils import atomic_write
        atomic_write(cooldown_path, json.dumps(cooldowns, indent=2) + "\n")
    except (ImportError, OSError) as e:
        _log_iteration("error", f"Failed to write diagnostic cooldown: {e}")


def _is_diagnostic_on_cooldown(
    instance_dir: str, project_name: str, cooldown_days: int,
) -> bool:
    """Check whether a project is still within the diagnostic cooldown window."""
    from datetime import datetime, timedelta

    cooldowns = _load_diagnostic_cooldowns(instance_dir)
    last_ts = cooldowns.get(project_name)
    if not last_ts:
        return False
    try:
        last_dt = datetime.fromisoformat(last_ts)
        return datetime.now() - last_dt < timedelta(days=cooldown_days)
    except (ValueError, TypeError):
        return False


def _maybe_inject_diagnostic_mission(
    project_name: str,
    instance_dir: str,
    autonomous_mode: str,
) -> Optional[str]:
    """Check if a project needs a diagnostic mission and inject it.

    Called after project selection in plan_iteration().  If the project's
    success rate is below the configured floor AND it has enough
    consecutive non-productive sessions, injects a diagnostic mission
    (tech_debt, dead_code, or audit) into the Pending section of
    missions.md.  A per-project cooldown prevents flooding.

    Args:
        project_name: Selected project name.
        instance_dir: Path to instance directory.
        autonomous_mode: Current autonomous mode (wait/review/implement/deep).

    Returns:
        The injected mission text if a diagnostic was queued, None otherwise.
    """
    try:
        from app.config import get_autonomous_health_config
        health_cfg = get_autonomous_health_config()
    except (ImportError, OSError) as e:
        _log_iteration("error", f"Autonomous health config load failed: {e}")
        return None

    if not health_cfg["enabled"]:
        return None

    # Mode gate: current mode must be >= configured minimum
    min_rank = _MODE_RANK.get(health_cfg["min_mode"], 2)
    current_rank = _MODE_RANK.get(autonomous_mode, 0)
    if current_rank < min_rank:
        return None

    # Cooldown gate
    if _is_diagnostic_on_cooldown(
        instance_dir, project_name, health_cfg["cooldown_days"],
    ):
        return None

    # Success rate gate
    try:
        from app.mission_metrics import get_project_success_rates
        rates = get_project_success_rates(instance_dir, [project_name], days=30)
        rate = rates.get(project_name, 0.5)
    except (ImportError, OSError) as e:
        _log_iteration("error", f"Health check success rate lookup failed: {e}")
        return None

    # Neutral rate (0.5) means insufficient data — skip
    if rate >= health_cfg["success_rate_floor"] or rate == 0.5:
        return None

    # Staleness gate
    try:
        from app.session_tracker import get_staleness_score
        staleness = get_staleness_score(instance_dir, project_name)
    except (ImportError, OSError) as e:
        _log_iteration("error", f"Health check staleness lookup failed: {e}")
        return None

    if staleness < health_cfg["staleness_floor"]:
        return None

    # All gates passed — select diagnostic type and inject
    diag_type = _select_diagnostic_type(instance_dir, project_name)
    mission_entry = (
        f"- [autonomous:health] [project:{project_name}] /{diag_type}"
    )

    try:
        from app.utils import insert_pending_mission
        missions_path = Path(instance_dir) / "missions.md"
        inserted = insert_pending_mission(missions_path, mission_entry)
    except (ImportError, OSError) as e:
        _log_iteration("error", f"Failed to inject diagnostic mission: {e}")
        return None

    if not inserted:
        _log_iteration("koan",
            f"Diagnostic mission for '{project_name}' already pending — skipped")
        return None

    _save_diagnostic_cooldown(instance_dir, project_name)
    _log_iteration("koan",
        f"Health diagnostic: injected /{diag_type} for '{project_name}' "
        f"(success_rate={rate:.0%}, staleness={staleness}, "
        f"cooldown={health_cfg['cooldown_days']}d)")

    return mission_entry


FilterResult = namedtuple("FilterResult", ["projects", "pr_limited", "branch_saturated", "focus_gated"],
                         defaults=[[]])
AutonomousDecision = namedtuple("AutonomousDecision", ["action", "focus_remaining"])


def _filter_exploration_projects(
    projects: List[Tuple[str, str]], koan_root: str,
    schedule_state=None,
) -> FilterResult:
    """Filter projects to only those eligible for exploration.

    Checks three gates in order:
    1. ``exploration`` flag — projects with ``exploration: false`` are excluded.
    2. ``max_open_prs`` limit — projects at or over their PR limit are excluded.
    3. ``max_pending_branches`` limit — projects at or over their branch limit
       are excluded.

    Returns a FilterResult with:
    - ``projects``: list of (name, path) tuples eligible for exploration
    - ``pr_limited``: list of project names excluded due to PR limit
    - ``branch_saturated``: list of project names excluded due to branch limit
    """
    from app.projects_config import (
        load_projects_config, get_project_exploration, get_project_focus,
        get_project_max_open_prs,
    )

    try:
        config = load_projects_config(koan_root)
    except (OSError, ValueError) as e:
        print(f"[iteration_manager] Could not load projects config: {e}", file=sys.stderr)
        return FilterResult(projects=projects, pr_limited=[], branch_saturated=[], focus_gated=[])

    if config is None:
        return FilterResult(projects=projects, pr_limited=[], branch_saturated=[], focus_gated=[])

    # Gate 0: focus flag — filter out projects with focus: true
    focus_gated = []
    not_focused = []
    for name, path in projects:
        if get_project_focus(config, name):
            _log_iteration("koan",
                f"Project '{name}' has focus: true — excluding from exploration")
            focus_gated.append(name)
        else:
            not_focused.append((name, path))

    # Gate 1: exploration flag
    exploration_enabled = [
        (name, path) for name, path in not_focused
        if get_project_exploration(config, name)
    ]

    # Gate 2: max_open_prs limit
    # During deep_hours, relax PR limits — allow exploration in review mode
    skip_pr_limit = False
    if schedule_state is not None:
        from app.schedule_manager import should_relax_pr_limit
        skip_pr_limit = should_relax_pr_limit(schedule_state)

    if skip_pr_limit:
        return FilterResult(projects=exploration_enabled, pr_limited=[], branch_saturated=[], focus_gated=focus_gated)

    from app.github import get_gh_username, batch_count_open_prs, cached_count_open_prs
    author = get_gh_username()

    # Phase 1: Collect all repos that need PR counts
    # Projects with limit=0, no author, or no URLs skip the PR check entirely
    projects_needing_check = {}  # name -> (path, limit, urls_to_check)
    filtered = []
    pr_limited = []

    for name, path in exploration_enabled:
        limit = get_project_max_open_prs(config, name)
        if limit == 0:
            filtered.append((name, path))
            continue

        if not author:
            filtered.append((name, path))
            continue

        project_cfg = config.get("projects", {}).get(name, {}) or {}
        urls_to_check = set()
        primary_url = project_cfg.get("github_url", "")
        if primary_url:
            urls_to_check.add(primary_url)
        for url in project_cfg.get("github_urls", []):
            if url:
                urls_to_check.add(url)

        if not urls_to_check:
            _log_iteration("debug",
                f"Project '{name}' has max_open_prs={limit} but no github_url — skipping PR check")
            filtered.append((name, path))
            continue

        projects_needing_check[name] = (path, limit, urls_to_check)

    if projects_needing_check:
        # Phase 2: Batch-fetch PR counts for all repos in one GraphQL call
        all_repos = []
        for (_, _, urls) in projects_needing_check.values():
            all_repos.extend(urls)
        all_repos = list(dict.fromkeys(all_repos))  # deduplicate, preserve order

        batch_results = batch_count_open_prs(all_repos, author)

        # Phase 3: Evaluate limits using batch results (fall back to sequential on miss)
        for name, (path, limit, urls_to_check) in projects_needing_check.items():
            total_open = 0
            any_error = False

            for url in urls_to_check:
                if url in batch_results:
                    count = batch_results[url]
                else:
                    # Batch missed this repo — fall back to individual query
                    count = cached_count_open_prs(url, author)
                if count >= 0:
                    total_open += count
                else:
                    any_error = True

            if any_error and total_open == 0:
                # All URLs errored — conservative: treat as PR-limited
                pr_limited.append(name)
                continue

            if total_open >= limit:
                _log_iteration("koan",
                    f"Project '{name}' at PR limit ({total_open}/{limit}) — excluding from exploration")
                pr_limited.append(name)
            else:
                filtered.append((name, path))

    # Gate 3: max_pending_branches limit
    from app.projects_config import get_project_max_pending_branches

    instance_dir = str(Path(koan_root) / "instance")
    branch_saturated = []
    final_filtered = []

    for name, path in filtered:
        branch_limit = get_project_max_pending_branches(config, name)
        if branch_limit == 0:
            final_filtered.append((name, path))
            continue

        project_cfg = config.get("projects", {}).get(name, {}) or {}
        urls = set()
        primary = project_cfg.get("github_url", "")
        if primary:
            urls.add(primary)
        for u in project_cfg.get("github_urls", []):
            if u:
                urls.add(u)

        try:
            from app.branch_limiter import count_pending_branches
            count = count_pending_branches(
                instance_dir, name, path, list(urls), author,
            )
        except Exception as e:
            _log_iteration("debug",
                f"Branch count failed for '{name}': {e} — allowing")
            final_filtered.append((name, path))
            continue

        if count >= branch_limit:
            _log_iteration("koan",
                f"Project '{name}' branch-saturated ({count}/{branch_limit}) "
                f"— excluding from exploration")
            branch_saturated.append(name)
        else:
            final_filtered.append((name, path))

    return FilterResult(projects=final_filtered, pr_limited=pr_limited,
                        branch_saturated=branch_saturated, focus_gated=focus_gated)


def _check_schedule():
    """Check schedule state (time-of-day windows from config).

    Returns:
        ScheduleState object, or None if schedule is not configured
        or module is unavailable.
    """
    try:
        from app.schedule_manager import get_current_schedule
        return get_current_schedule()
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Schedule check failed: {e}")
        return None


def _make_result(*, action, project_name, project_path="",
                 mission_title="", autonomous_mode, focus_area="",
                 available_pct, decision_reason, display_lines,
                 recurring_injected, focus_remaining=None,
                 passive_remaining=None,
                 schedule_mode="normal", error=None,
                 tracker_error=None, cost_today=0.0,
                 mission_tier=None):
    """Build a standardised iteration-plan result dict."""
    return {
        "action": action,
        "project_name": project_name,
        "project_path": project_path or "",
        "mission_title": mission_title,
        "autonomous_mode": autonomous_mode,
        "focus_area": focus_area,
        "available_pct": available_pct,
        "decision_reason": decision_reason,
        "display_lines": display_lines,
        "recurring_injected": recurring_injected,
        "focus_remaining": focus_remaining,
        "passive_remaining": passive_remaining,
        "schedule_mode": schedule_mode,
        "error": error,
        "tracker_error": tracker_error,
        "cost_today": cost_today,
        "mission_tier": mission_tier,
    }


def _decide_autonomous_action(
    autonomous_mode: str,
    koan_root: str,
    schedule_state,
    contemplative_chance: int = 10,
    focus_mode: bool = False,
) -> "AutonomousDecision":
    """Decide autonomous action via a linear priority chain.

    Called when no mission is pending and WAIT mode has already been
    handled upstream (before exploration filtering).

    Priority (first match wins):
    1. Contemplative session — random roll, requires deep/implement + no focus
    2. Focus wait — focus mode active, skip exploration
    3. Schedule wait — work_hours active, skip exploration
    4. Autonomous exploration — default fallback

    When ``focus_mode`` is True (config-level or file-based), contemplation
    and exploration are disabled — the loop idles via ``focus_wait``.

    Returns:
        AutonomousDecision(action, focus_remaining)
    """
    focus_state = _check_focus(koan_root)
    focus_active = focus_state is not None or focus_mode
    _log_iteration("koan",
        f"Evaluating autonomous action "
        f"(mode={autonomous_mode}, focus_active={focus_active}, "
        f"focus_mode={focus_mode})")

    # 1. Contemplative session (random reflection)
    if _should_contemplate(autonomous_mode, focus_active,
                           contemplative_chance, schedule_state,
                           focus_mode=focus_mode):
        return AutonomousDecision(action="contemplative", focus_remaining=None)

    # 2. Focus mode active → wait for missions (file-based or config-level)
    if focus_state is not None:
        try:
            focus_remaining = focus_state.remaining_display()
        except (ValueError, OSError) as e:
            _log_iteration("error", f"Focus state display error: {e}")
            focus_remaining = "unknown"
        return AutonomousDecision(action="focus_wait",
                                 focus_remaining=focus_remaining)

    # 2b. Config-level focus mode (permanent, no remaining time)
    if focus_mode:
        return AutonomousDecision(action="focus_wait",
                                 focus_remaining="permanent")

    # 3. Schedule work_hours → suppress exploration
    if schedule_state is not None and schedule_state.in_work_hours:
        return AutonomousDecision(action="schedule_wait", focus_remaining=None)

    # 4. Default: autonomous exploration
    return AutonomousDecision(action="autonomous", focus_remaining=None)


def plan_iteration(
    instance_dir: str,
    koan_root: str,
    run_num: int,
    count: int,
    projects: List[Tuple[str, str]],
    last_project: str = "",
    usage_state_path: str = "",
) -> dict:
    """Plan a single iteration of the run loop.

    This is the main entry point. It consolidates all per-iteration
    decision-making into a single call.

    Args:
        instance_dir: Path to instance directory
        koan_root: Path to KOAN_ROOT
        run_num: Current run number (1-based)
        count: Completed runs count
        projects: List of (name, path) tuples
        last_project: Last project name (for rotation)
        usage_state_path: Path to usage_state.json (defaults to instance/usage_state.json)

    Returns:
        dict with iteration plan:
        {
            "action": "mission" | "autonomous" | "contemplative" | "passive_wait" | "focus_wait" | "schedule_wait" | "exploration_wait" | "pr_limit_wait" | "wait_pause" | "error",
            "project_name": str,
            "project_path": str,
            "mission_title": str (empty for autonomous/contemplative),
            "autonomous_mode": str (wait/review/implement/deep),
            "focus_area": str,
            "available_pct": int,
            "decision_reason": str,
            "display_lines": list[str] (usage status lines for console),
            "recurring_injected": list[str] (injected recurring missions),
            "focus_remaining": str | None (if focus mode active),
            "schedule_mode": str (deep/work/normal from schedule config),
            "error": str | None (project validation error),
        }
    """
    instance = Path(instance_dir)
    if usage_state_path:
        usage_state = Path(usage_state_path)
    else:
        usage_state = instance / "usage_state.json"
    usage_md = instance / "usage.md"

    # Convert projects to string format for downstream functions
    projects_str = _projects_to_str(projects)

    # Step 0: Detect config-level focus mode (disables autonomous work)
    try:
        from app.config import is_focus_mode
        focus_mode = is_focus_mode()
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Focus mode config lookup failed: {e}")
        focus_mode = False

    # Step 0b: Process overdue one-shot event triggers.
    try:
        from app.event_scheduler import tick as _event_tick
        _enqueued = _event_tick(instance_dir)
        for _m in _enqueued:
            _log_iteration("koan", f"[event] Enqueued scheduled mission: {_m[:80]}")
    except (ImportError, OSError) as e:
        _log_iteration("error", f"Event scheduler tick failed: {e}")

    # Step 1: Refresh usage
    _refresh_usage(usage_state, usage_md, count)

    # Step 1b: Warn the human when the rolling burn rate predicts a near-future
    # quota wipeout. Fires at most once per quota cycle.
    _maybe_warn_burn_rate(instance, usage_state)

    # Step 2: Get usage decision (mode, available%, reason, project idx)
    decision = _get_usage_decision(usage_md, count, projects_str)
    autonomous_mode = decision["mode"]
    available_pct = decision["available_pct"]
    decision_reason = decision["reason"]
    display_lines = decision["display_lines"]
    tracker_error = decision.get("tracker_error")
    usage_tracker = decision.get("tracker")  # None on tracker error path
    cost_today = decision.get("cost_today", 0.0)
    _log_iteration("koan", f"Usage decision: mode={autonomous_mode}, available={available_pct}%")

    # Step 2a: Cap mode at implement when focus mode is active.
    # DEEP mode encourages autonomous GitHub issue pickup, which focus
    # mode explicitly forbids — missions only, no autonomous work.
    if focus_mode and autonomous_mode == "deep":
        decision_reason = (
            f"{decision_reason} (capped from deep: focus mode active)"
        )
        autonomous_mode = "implement"
        _log_iteration("koan",
            "Focus mode: capped mode deep → implement")

    # Step 2b: Check schedule and cap mode based on deep_hours config.
    # This runs early (before mission pick) so the capped mode affects
    # everything downstream — including the prompt sent for missions.
    schedule_state = _check_schedule()
    deep_hours_configured = False
    try:
        from app.schedule_manager import get_schedule_config, cap_mode_for_schedule
        deep_spec, _ = get_schedule_config()
        deep_hours_configured = bool(deep_spec.strip())
        if schedule_state is not None:
            original_mode = autonomous_mode
            autonomous_mode = cap_mode_for_schedule(
                autonomous_mode, schedule_state, deep_hours_configured,
            )
            if autonomous_mode != original_mode:
                decision_reason = (
                    f"{decision_reason} (capped from {original_mode}: "
                    f"outside deep_hours schedule)"
                )
    except (ImportError, OSError, ValueError) as e:
        _log_iteration("error", f"Schedule mode cap check failed: {e}")

    # Step 3: Inject recurring missions
    recurring_injected = _inject_recurring(instance)

    # Step 3b: Drain CI queue (one entry per iteration, non-blocking)
    ci_drain_msg = _drain_ci_queue(instance)

    # Step 3c: Auto-dispatch CI fix missions for failing Koan PRs
    _dispatch_ci_fixes(instance, koan_root)

    # Step 4: Pick mission. Manual missions (queued in missions.md or via
    # notifications) are always eligible regardless of branch saturation —
    # max_pending_branches is a self-throttle for autonomous exploration,
    # not a gate on human instructions. Saturation is enforced by
    # _filter_exploration_projects in the no-mission path only.
    mission_project, mission_title = _pick_mission(
        instance, projects_str, run_num, autonomous_mode, last_project,
    )
    if mission_project and mission_title:
        _log_iteration("mission",
            f"Mission picked: [{mission_project}] {mission_title[:80]}")
    else:
        _log_iteration("koan", "No pending mission — entering autonomous mode")

    # Step 4b: Passive mode gate — block all execution
    # Missions stay Pending, no autonomous work. Must check before start_mission().
    passive_state = _check_passive(koan_root)
    if passive_state is not None:
        remaining = passive_state.remaining_display()
        _log_iteration("koan", f"Passive mode active ({remaining}) — skipping execution")
        return _make_result(
            action="passive_wait",
            project_name=mission_project or (projects[0][0] if projects else "default"),
            project_path="",
            mission_title="",
            autonomous_mode=autonomous_mode,
            focus_area="Passive mode: read-only, no execution",
            available_pct=available_pct,
            decision_reason=f"Passive mode — read-only ({remaining})",
            display_lines=display_lines,
            recurring_injected=recurring_injected,
            focus_remaining=None,
            schedule_mode=schedule_state.mode if schedule_state else "normal",
            tracker_error=tracker_error,
            passive_remaining=remaining,
        )

    # Step 5: Resolve project for the picked mission.
    if mission_project and mission_title:
        resolved = _resolve_project_path(mission_project, projects)

        if resolved is None:
            project_name = mission_project
            project_path = None
        else:
            project_name, project_path = resolved

        if project_path is None:
            known = _get_known_project_names(projects)
            return _make_result(
                action="error",
                project_name=project_name,
                mission_title=mission_title,
                autonomous_mode=autonomous_mode,
                available_pct=available_pct,
                decision_reason=decision_reason,
                display_lines=display_lines,
                recurring_injected=recurring_injected,
                schedule_mode=schedule_state.mode if schedule_state else "normal",
                error=f"Unknown project '{project_name}'. Known: {', '.join(known)}",
                tracker_error=tracker_error,
            )

    # Step 5b: Pre-classify mission complexity (when a mission was picked
    # and project resolved successfully).  Cache the tier in missions.md
    # so re-runs skip the classifier call entirely.
    mission_tier: Optional[str] = None
    if mission_project and mission_title and project_path is not None:
        mission_tier = _classify_mission(
            mission_title, project_name, instance / "missions.md"
        )

        # Step 5c: Re-check affordability now that tier is known.
        # Tier-based model upgrades (e.g. complex → opus) can increase
        # cost 2-3x.  The initial budget guard (Step 2) ran before tier
        # classification, so it used the base mode multiplier only.
        if mission_tier and usage_tracker is not None:
            tier_mult = _get_tier_cost_multiplier(mission_tier, project_name)
            if tier_mult > 1.0:
                prev_mode = autonomous_mode
                autonomous_mode = _downgrade_if_unaffordable(
                    usage_tracker, autonomous_mode,
                    tier_multiplier=tier_mult,
                )
                if autonomous_mode != prev_mode:
                    decision_reason = (
                        f"{decision_reason} (tier '{mission_tier}' "
                        f"recheck: {prev_mode} → {autonomous_mode})"
                    )

    else:
        # No mission — autonomous mode
        mission_title = ""

        # Short-circuit: WAIT mode means budget is exhausted — skip
        # exploration filtering entirely to avoid wasted gh API calls.
        if autonomous_mode == "wait":
            focus_area = resolve_focus_area(autonomous_mode, has_mission=False)
            return _make_result(
                action="wait_pause",
                project_name=projects[0][0] if projects else "default",
                project_path=projects[0][1] if projects else "",
                autonomous_mode=autonomous_mode,
                focus_area=focus_area,
                available_pct=available_pct,
                decision_reason=decision_reason,
                display_lines=display_lines,
                recurring_injected=recurring_injected,
                schedule_mode=schedule_state.mode if schedule_state else "normal",
                tracker_error=tracker_error,
            )

        # Short-circuit: config-level focus mode means no autonomous work.
        # Skip exploration filtering, contemplative rolls, and any gh calls —
        # idle with wake-on-mission like exploration_wait.
        if focus_mode:
            _log_iteration("koan",
                "Focus mode: no pending mission — entering focus_wait")
            focus_area = resolve_focus_area(autonomous_mode, has_mission=False)
            return _make_result(
                action="focus_wait",
                project_name=projects[0][0] if projects else "default",
                project_path=projects[0][1] if projects else "",
                autonomous_mode=autonomous_mode,
                focus_area=focus_area,
                available_pct=available_pct,
                decision_reason=(
                    "Focus mode — no autonomous work, "
                    "waiting for queued missions"
                ),
                display_lines=display_lines,
                recurring_injected=recurring_injected,
                schedule_mode=schedule_state.mode if schedule_state else "normal",
                tracker_error=tracker_error,
            )

        # Filter to exploration-enabled projects only
        filter_result = _filter_exploration_projects(projects, koan_root,
                                                     schedule_state=schedule_state)
        exploration_projects = filter_result.projects
        if not exploration_projects:
            # Determine whether this is focus-gated, exploration-disabled, PR-limited, or branch-saturated
            if filter_result.focus_gated:
                _log_iteration("koan", "All projects have focus enabled — waiting for queued missions")
                wait_action = "exploration_wait"
                wait_reason = "All projects have focus enabled — waiting for queued missions"
            elif filter_result.branch_saturated:
                _log_iteration("koan", "All exploration projects branch-saturated — waiting for reviews")
                wait_action = "branch_saturated_wait"
                wait_reason = (
                    f"Branch limit reached for: {', '.join(filter_result.branch_saturated)} "
                    f"— waiting for reviews/merges"
                )
            elif filter_result.pr_limited:
                _log_iteration("koan", "All exploration projects at PR limit — waiting for reviews")
                wait_action = "pr_limit_wait"
                wait_reason = (
                    f"PR limit reached for: {', '.join(filter_result.pr_limited)} "
                    f"— waiting for reviews"
                )
            else:
                _log_iteration("koan", "All projects have exploration disabled — waiting for missions")
                wait_action = "exploration_wait"
                wait_reason = "All projects have exploration disabled — waiting for missions"

            focus_area = resolve_focus_area(autonomous_mode, has_mission=False)
            return _make_result(
                action=wait_action,
                project_name=projects[0][0] if projects else "default",
                project_path=projects[0][1] if projects else "",
                autonomous_mode=autonomous_mode,
                focus_area=focus_area,
                available_pct=available_pct,
                decision_reason=wait_reason,
                display_lines=display_lines,
                recurring_injected=recurring_injected,
                schedule_mode=schedule_state.mode if schedule_state else "normal",
                tracker_error=tracker_error,
            )

        project_name, project_path = _select_random_exploration_project(
            exploration_projects, last_project,
            instance_dir=instance_dir,
        )
        _log_iteration("koan",
            f"Exploration: selected '{project_name}' "
            f"from {len(exploration_projects)} eligible project(s)"
            f"{' (avoiding last: ' + last_project + ')' if last_project and last_project != project_name else ''}")

        # Step 5c: Health diagnostic gate — inject a diagnostic mission
        # for projects with persistently low success rates.
        if instance_dir:
            _maybe_inject_diagnostic_mission(
                project_name, instance_dir, autonomous_mode,
            )

    # Step 6: Determine action for autonomous mode
    if mission_title:
        action = "mission"
    else:
        # No mission — decide autonomous action via priority chain
        try:
            from app.utils import get_contemplative_chance
            contemplative_chance = get_contemplative_chance()
        except (ImportError, OSError, ValueError) as e:
            _log_iteration("error", f"Contemplative chance load error: {e}")
            contemplative_chance = 10

        # Adapt chance based on historical contemplative productivity
        adapted_chance = contemplative_chance
        if project_name and instance_dir:
            with suppress_logged(_log_iteration, "error", "Contemplative chance adaptation failed",
                                 ImportError, OSError, ValueError):
                from app.session_tracker import adapt_contemplative_chance
                adapted_chance = adapt_contemplative_chance(
                    contemplative_chance, instance_dir, project_name
                )
                if adapted_chance != contemplative_chance:
                    _log_iteration("koan",
                        f"Contemplative chance adapted: "
                        f"{contemplative_chance}% → {adapted_chance}% "
                        f"(project={project_name})")

        autonomous_decision = _decide_autonomous_action(
            autonomous_mode, koan_root, schedule_state, adapted_chance,
            focus_mode=focus_mode,
        )
        action = autonomous_decision.action

        if action == "contemplative" and adapted_chance != contemplative_chance:
            decision_reason = (
                f"contemplative (adapted {contemplative_chance}%→{adapted_chance}%)"
            )

        # Side effect: maybe suggest automations (non-blocking).
        # If action is autonomous/contemplative, focus is already inactive
        # (otherwise _decide_autonomous_action would have returned focus_wait).
        if action in ("autonomous", "contemplative") and project_name and project_path:
            try:
                from app.suggestion_engine import maybe_suggest_automations
                if maybe_suggest_automations(
                    instance_dir, project_name, project_path,
                    autonomous_mode, focus_active=False,
                ):
                    _log_iteration("koan", f"Sent automation suggestions for {project_name}")
            except Exception as e:
                _log_iteration("error", f"Suggestion engine error: {e}")

        if action == "focus_wait":
            focus_area = resolve_focus_area(autonomous_mode, has_mission=False)
            return _make_result(
                action=action,
                project_name=project_name,
                project_path=project_path,
                autonomous_mode=autonomous_mode,
                focus_area=focus_area,
                available_pct=available_pct,
                decision_reason=decision_reason,
                display_lines=display_lines,
                recurring_injected=recurring_injected,
                focus_remaining=autonomous_decision.focus_remaining,
                schedule_mode=schedule_state.mode if schedule_state else "normal",
                tracker_error=tracker_error,
            )

        if action == "schedule_wait":
            focus_area = resolve_focus_area(autonomous_mode, has_mission=False)
            return _make_result(
                action=action,
                project_name=project_name,
                project_path=project_path,
                autonomous_mode=autonomous_mode,
                focus_area=focus_area,
                available_pct=available_pct,
                decision_reason=decision_reason,
                display_lines=display_lines,
                recurring_injected=recurring_injected,
                schedule_mode="work",
                tracker_error=tracker_error,
            )

    # Step 7: Resolve focus area
    has_mission = bool(mission_title)
    focus_area = resolve_focus_area(autonomous_mode, has_mission=has_mission)

    return _make_result(
        action=action,
        project_name=project_name,
        project_path=project_path,
        mission_title=mission_title,
        autonomous_mode=autonomous_mode,
        focus_area=focus_area,
        available_pct=available_pct,
        decision_reason=decision_reason,
        display_lines=display_lines,
        recurring_injected=recurring_injected,
        schedule_mode=schedule_state.mode if schedule_state else "normal",
        tracker_error=tracker_error,
        cost_today=cost_today,
        mission_tier=mission_tier,
    )


def main():
    """CLI entry point for iteration_manager."""
    global _cli_mode
    _cli_mode = True
    parser = argparse.ArgumentParser(description="Kōan iteration planner")
    subparsers = parser.add_subparsers(dest="command")

    plan_parser = subparsers.add_parser("plan-iteration",
                                        help="Plan next loop iteration")
    plan_parser.add_argument("--instance", required=True, help="Instance directory")
    plan_parser.add_argument("--koan-root", required=True, help="KOAN_ROOT directory")
    plan_parser.add_argument("--run-num", type=int, required=True, help="Current run number (1-based)")
    plan_parser.add_argument("--count", type=int, required=True, help="Completed runs count")
    plan_parser.add_argument("--projects", required=True, help="Projects string (name:path;...)")
    plan_parser.add_argument("--last-project", default="", help="Last project name")
    plan_parser.add_argument("--usage-state", required=True, help="Path to usage_state.json")

    args = parser.parse_args()

    if args.command == "plan-iteration":
        # Convert CLI string format to tuples
        projects = []
        for pair in args.projects.split(";"):
            pair = pair.strip()
            if pair:
                parts = pair.split(":", 1)
                if len(parts) == 2:
                    projects.append((parts[0].strip(), parts[1].strip()))
        result = plan_iteration(
            instance_dir=args.instance,
            koan_root=args.koan_root,
            run_num=args.run_num,
            count=args.count,
            projects=projects,
            last_project=args.last_project,
            usage_state_path=args.usage_state,
        )
        print(json.dumps(result))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
