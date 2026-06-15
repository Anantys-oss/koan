"""Mission execution lifecycle — extracted from run.py.

Contains the per-iteration mission dispatch, retry, and execution logic:
- _handle_skill_dispatch: skill command detection and execution
- _get_git_head: git HEAD snapshot for retry safety
- _maybe_retry_mission: single-retry on transient CLI errors
- _run_iteration: full iteration body (planning, dispatch, execution, finalization)
"""

import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Mission retry constants
# ---------------------------------------------------------------------------

_MISSION_MAX_RETRIES = 1
_MISSION_RETRY_DELAY = 10  # seconds

_last_idle_msg = ""  # dedup consecutive identical idle-wait log lines


def _handle_skill_dispatch(
    mission_title: str,
    project_name: str,
    project_path: str,
    koan_root: str,
    instance: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    interval: int,
    mission_tier: str = "",
) -> tuple:
    """Try to dispatch a mission as a skill command.

    Returns:
        (handled: bool, mission_title: str) — if handled is True the caller
        should return immediately; if False the caller should proceed to Claude
        using the returned mission_title (which may have been translated by a
        cli_skill mapping).
    """
    import app.run as _run
    log = _run.log
    suppress_logged = _run.suppress_logged

    from app.debug import debug_log as _debug_log
    preview = f"{mission_title[:100]}..." if len(mission_title) > 100 else mission_title
    _debug_log(f"[run] checking skill dispatch for: {preview}")

    from app.skill_dispatch import dispatch_skill_mission, is_skill_mission
    skill_cmd = dispatch_skill_mission(
        mission_text=mission_title,
        project_name=project_name,
        project_path=project_path,
        koan_root=koan_root,
        instance_dir=instance,
    )
    if skill_cmd:
        _debug_log(f"[run] skill dispatch matched: {' '.join(skill_cmd[:5])}")
        log("mission", "Decision: SKILL DISPATCH (direct runner)")
        log("mission", f"Mission: {mission_title}")
        log("mission", f"Project: {project_name}")
        log("mission", f"Runner: {' '.join(skill_cmd[:4])}...")
        _run.set_status(koan_root, f"Run {run_num}/{max_runs} — skill dispatch on {project_name}")
        _run._notify(instance, f"🚀 [{project_name}] Run {run_num}/{max_runs} — Skill: {mission_title}")

        # Create pending.md so /live can show progress during skill dispatch
        from app.loop_manager import create_pending_file
        try:
            create_pending_file(
                instance_dir=instance,
                project_name=project_name,
                run_num=run_num,
                max_runs=max_runs,
                autonomous_mode=autonomous_mode or "implement",
                mission_title=mission_title,
            )
        except Exception as e:
            log("error", f"Failed to create pending.md for skill dispatch: {e}")

        exit_code = 1
        skill_result = {"exit_code": 1, "stdout": "", "stderr": "",
                        "quota_exhausted": False, "quota_info": None}
        # Snapshot core files before skill execution
        from app.core_files import snapshot_core_files, check_core_files, log_integrity_warnings
        skill_core_snapshot = snapshot_core_files(koan_root, project_path)

        try:
            with _run.protected_phase(f"Skill: {mission_title[:50]}"):
                skill_result = _run._run_skill_mission(
                    skill_cmd=skill_cmd,
                    koan_root=koan_root,
                    instance=instance,
                    project_name=project_name,
                    project_path=project_path,
                    run_num=run_num,
                    mission_title=mission_title,
                    autonomous_mode=autonomous_mode,
                    mission_tier=mission_tier,
                )
            exit_code = skill_result["exit_code"]
            if exit_code == 0:
                log("mission", f"Run {run_num}/{max_runs} — [{project_name}] skill completed")

            # Verify core files survived skill execution
            skill_integrity = check_core_files(koan_root, skill_core_snapshot, project_path)
            if skill_integrity:
                from app.core_files import recover_project_files
                missing = skill_core_snapshot - snapshot_core_files(koan_root, project_path)
                recovered, unrecoverable = recover_project_files(missing, project_path)
                if recovered:
                    log("core_files", f"Auto-recovered {len(recovered)} file(s): {', '.join(recovered)}")
                if unrecoverable:
                    log_integrity_warnings(unrecoverable)
                    log("error", f"Core file integrity check failed after skill: {len(unrecoverable)} file(s) unrecoverable")
                    exit_code = 1
        except KeyboardInterrupt:
            log("error", "Skill dispatch interrupted by user")
            _run._finalize_mission(instance, mission_title, project_name, 1)
            raise
        except Exception as e:
            log("error", f"Skill dispatch exception: {e}\n{traceback.format_exc()}")
        finally:
            # Clean up temp files created by skill command builders
            from app.skill_dispatch import cleanup_skill_temp_files
            cleanup_skill_temp_files(skill_cmd)

        _skill_provider_name, _skill_provider_label = _run._provider_identity()
        _skill_stdout = skill_result.get("stdout", "")
        _skill_stderr = skill_result.get("stderr", "")
        _skill_hqe = dict(
            stdout_text=_skill_stdout,
            stderr_text=_skill_stderr,
            exit_code=exit_code,
        )

        # --- Auth / quota classification ---
        # Skill stdout is a summarized agent transcript (DATA): it quotes CI
        # logs, failing tests, and Kōan's own identifiers — e.g. /ci_check
        # always prints ``"quota_exhausted": false``. Scanning it for quota
        # falsely paused the daemon, so classify from stderr only. Genuine
        # skill quota arrives via the structured ``quota_exhausted`` field
        # below, not from the transcript.
        if _run._classify_and_handle_cli_error(
            exit_code, _skill_stdout, _skill_stderr,
            provider_name=_skill_provider_name,
            provider_label=_skill_provider_label,
            koan_root=koan_root,
            instance=instance,
            project_name=project_name,
            mission_title=mission_title,
            run_num=run_num,
            hqe_kwargs=_skill_hqe,
            trust_stdout=False,
        ):
            return True, mission_title

        # --- Exit-0 quota probe ---
        # For skill dispatches, only check stderr (which IS CLI output).
        # Skill stdout contains summarized runner text where assistant
        # responses can mention "quota" or "hit your limit" and trip
        # false-positive detection.  (Fixes #1618)
        if exit_code == 0 and not skill_result.get("quota_exhausted"):
            _skill_hqe_stderr_only = dict(
                stdout_text="",
                stderr_text=_skill_stderr,
                exit_code=exit_code,
            )
            if _run._probe_exit0_quota(
                provider_name=_skill_provider_name,
                provider_label=_skill_provider_label,
                koan_root=koan_root,
                instance=instance,
                mission_title=mission_title,
                run_num=run_num,
                hqe_kwargs=_skill_hqe_stderr_only,
                project_name=project_name,
            ):
                return True, mission_title

        # --- Post-mission quota exhaustion (detected during pipeline) ---
        if skill_result.get("quota_exhausted"):
            _run._handle_pipeline_quota_flag(
                provider_label=_skill_provider_label,
                koan_root=koan_root,
                instance=instance,
                mission_title=mission_title,
                count=run_num,
                quota_info=skill_result.get("quota_info"),
            )
            return True, mission_title

        # Suppress redundant notification when the skill already notified
        # the user directly (e.g. fix_runner sends "⏭ Issue already closed").
        _skill_stdout = skill_result.get("stdout", "")
        _skill_already_notified = (
            exit_code == 0
            and "— skipping" in _skill_stdout
        )
        if not _skill_already_notified:
            _run._notify_mission_end(
                instance, project_name, run_num, max_runs,
                exit_code, mission_title,
            )
        _run._finalize_mission(instance, mission_title, project_name, exit_code)
        _run._commit_instance(instance)

        _run._sleep_between_runs(koan_root, instance, interval)
        return True, mission_title

    # Check for cli_skill translation before failing unrecognized /commands
    if is_skill_mission(mission_title):
        from pathlib import Path as _Path
        from app.skill_dispatch import (
            translate_cli_skill_mission,
            strip_passthrough_command,
            expand_combo_skill,
        )

        # Combo skills (e.g. /rr) are bridge-side handlers that queue
        # multiple sub-missions. Expand them and mark the original done.
        if expand_combo_skill(mission_title, instance):
            log("mission", "Decision: COMBO EXPAND (sub-missions queued)")
            _run._notify(instance, f"🔀 [{project_name}] Combo skill expanded into sub-missions")
            _run._finalize_mission(instance, mission_title, project_name, exit_code=0)
            _run._commit_instance(instance)
            return True, mission_title

        # Some /commands (e.g. /gh_request) are bridge-side handlers that
        # can also land in the mission queue via GitHub notifications.
        # Strip the prefix and let Claude handle them as regular missions.
        passthrough_text = strip_passthrough_command(mission_title)
        if passthrough_text is not None:
            _debug_log(
                f"[run] passthrough command: '{mission_title}' -> '{passthrough_text}'"
            )
            log("mission", "Decision: PASSTHROUGH (command stripped, sending to Claude)")
            return False, passthrough_text

        translated = translate_cli_skill_mission(
            mission_text=mission_title,
            koan_root=_Path(koan_root),
            instance_dir=_Path(instance),
        )
        if translated is not None:
            _debug_log(
                f"[run] cli_skill translation: '{mission_title[:80]}' -> '{translated[:80]}'"
            )
            log("mission", "Decision: CLI SKILL (provider slash command)")
            # Return untranslated=False so caller falls through to Claude with translated title
            return False, translated

        _debug_log(f"[run] skill mission unhandled, failing: {mission_title[:200]}")

        # Differentiate "unknown command" from "known command, bad arguments"
        from app.skill_dispatch import parse_skill_mission, validate_skill_args
        _, cmd_name, cmd_args = parse_skill_mission(mission_title)
        arg_error = validate_skill_args(cmd_name, cmd_args) if cmd_name else None
        if arg_error:
            log("warning", f"Skill mission invalid args: {arg_error}")
            _run._notify(instance, f"⚠️ [{project_name}] {arg_error}")
        else:
            log("warning", f"Skill mission has no runner, failing: {mission_title[:80]}")
            _run._notify(instance, f"⚠️ [{project_name}] Unknown skill command: {mission_title[:80]}")
        _run._finalize_mission(instance, mission_title, project_name, exit_code=1)
        _run._commit_instance(instance)
        return True, mission_title

    return False, mission_title


def _get_git_head(project_path: str) -> str:
    """Get current git HEAD SHA for retry safety check."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _maybe_retry_mission(
    claude_exit: int,
    stdout_file: str,
    stderr_file: str,
    cmd: list,
    project_path: str,
    pre_head: str,
    instance: str,
    project_name: str,
    run_num: int,
    has_mission: bool,
    provider_name: str = "",
) -> tuple:
    """Attempt a single retry if the CLI error is transient.

    Returns ``(exit_code, stdout_file, stderr_file)`` — the files may
    be replaced if a retry was performed (old files are truncated to
    avoid double-counting output).

    Only retries if:
    - The error is classified as RETRYABLE
    - No commits were produced (HEAD didn't move)
    - This is a mission (not autonomous), since missions are higher-value
    """
    import app.run as _run
    log = _run.log
    suppress_logged = _run.suppress_logged

    from app.cli_errors import ErrorCategory, classify_cli_error

    # Watchdog timeouts are NOT transient — don't retry a session that ran
    # for the full timeout duration.  Without this guard, "timeout" in the
    # agent's output text (test logs, error messages) would match the
    # RETRYABLE pattern and start another full-length session.
    if _run._last_mission_timed_out:
        log("koan", "Skipping retry — mission was killed by watchdog timeout")
        return claude_exit, stdout_file, stderr_file

    # User-initiated aborts must not be retried — the user explicitly asked
    # to stop this mission.
    if _run._last_mission_aborted:
        log("koan", "Skipping retry — mission was aborted by user")
        return claude_exit, stdout_file, stderr_file

    # Stagnated sessions have their own retry logic in _finalize_mission
    # (requeue with counter tracking).  Retrying here would clear the
    # _last_mission_stagnated flag, causing _finalize_mission to miss
    # the stagnation event entirely.
    if _run._last_mission_stagnated.is_set():
        log("koan", "Skipping retry — mission was killed by stagnation monitor")
        return claude_exit, stdout_file, stderr_file

    # Read output for classification
    try:
        stdout_text = Path(stdout_file).read_text()
    except OSError:
        stdout_text = ""
    try:
        stderr_text = Path(stderr_file).read_text()
    except OSError:
        stderr_text = ""

    category = classify_cli_error(
        claude_exit,
        stdout_text,
        stderr_text,
        provider_name=provider_name,
    )
    log("error", f"CLI error classified as {category.value} (exit={claude_exit})")

    if category != ErrorCategory.RETRYABLE:
        return claude_exit, stdout_file, stderr_file

    if not has_mission:
        log("koan", "Skipping retry for autonomous run (lower priority)")
        return claude_exit, stdout_file, stderr_file

    # Safety: don't retry if Claude already produced commits
    post_head = _run._get_git_head(project_path)
    if pre_head and post_head and pre_head != post_head:
        log("koan", "Skipping retry — commits were produced before the error")
        return claude_exit, stdout_file, stderr_file

    log("koan", f"Transient CLI error — retrying mission in {_MISSION_RETRY_DELAY}s")
    with _run.protected_phase("Mission retry backoff"):
        time.sleep(_MISSION_RETRY_DELAY)

    # Clear output files before retry to avoid double-counting
    with suppress_logged(log, "debug", "Output file clear before retry failed", OSError):
        open(stdout_file, "w").close()
        open(stderr_file, "w").close()

    retry_exit = _run.run_claude_task(
        cmd, stdout_file, stderr_file, cwd=project_path,
        instance_dir=instance, project_name=project_name, run_num=run_num,
    )
    log("koan", f"Mission retry exit_code={retry_exit}")
    return retry_exit, stdout_file, stderr_file


# ---------------------------------------------------------------------------
# Iteration body (extracted for exception isolation)
# ---------------------------------------------------------------------------

def _run_iteration(
    koan_root: str,
    instance: str,
    projects: list,
    count: int,
    max_runs: int,
    interval: int,
    git_sync_interval: int,
):
    """Execute a single iteration of the main loop.

    Called from main_loop() within a try/except block that catches
    unexpected exceptions without killing the process.

    Returns:
        True if this was a productive iteration (mission, autonomous, or
        contemplative session that consumed API budget).  ``"idle"`` for
        idle wait states (PR limit, schedule, focus, exploration).  False
        for other non-productive iterations (errors, dedup skips,
        preflight failures).  The caller only increments ``count`` on
        productive iterations so that ``max_runs`` reflects actual work
        done, not loop cycles.

    Exceptions:
        KeyboardInterrupt: Propagates to caller (user abort)
        SystemExit: Propagates to caller (restart signal)
        Exception: Caught by caller for recovery
    """
    import app.run as _run
    from app.run_log import _reset_terminal
    log = _run.log
    suppress_logged = _run.suppress_logged
    bold_cyan = _run.bold_cyan
    bold_green = _run.bold_green
    plan_iteration = _run.plan_iteration
    interruptible_sleep = _run.interruptible_sleep
    check_pending_missions = _run.check_pending_missions
    atomic_write = _run.atomic_write

    run_num = count + 1
    _run.set_status(koan_root, f"Run {run_num}/{max_runs} — preparing")

    # Write run-loop heartbeat so external monitors can detect a hung agent
    from app.health_check import write_run_heartbeat
    write_run_heartbeat(koan_root)

    log("run", bold_cyan(f"=== Run {run_num}/{max_runs} — {time.strftime('%Y-%m-%d %H:%M:%S')} ==="))

    # Refresh project list (picks up workspace changes since startup)
    from app.utils import get_known_projects
    refreshed = get_known_projects()
    if refreshed:
        # Filter out projects whose directories no longer exist
        valid = []
        for name, path in refreshed:
            if Path(path).is_dir():
                valid.append((name, path))
            elif name not in _run._warned_missing_projects:
                _run._warned_missing_projects.add(name)
                log("warn", f"Project '{name}' directory missing: {path} — skipping. "
                    f"Remove it from projects.yaml to silence this warning.")
        if valid:
            projects = valid

    # Per-phase Telegram visibility for the first iteration only. After
    # process start or /resume, count is 0 and the first iteration runs
    # several slow steps (GH cold-start, Jira scan, plan_iteration) that
    # together take ~30-90s before any mission notification fires. Surface
    # progress to Telegram so the human knows what's happening. count>=1
    # iterations stay quiet to avoid steady-state spam.
    is_first_iteration = not _run._startup_notified
    is_boot_iteration = not _run._boot_notified
    _run._startup_notified = True
    _run._boot_notified = True

    # Load config once for both GitHub and Jira gating below
    from app.utils import load_config
    from app.github_config import get_github_commands_enabled
    from app.jira_config import get_jira_enabled
    _boot_config = load_config()
    github_enabled = get_github_commands_enabled(_boot_config)
    jira_enabled = get_jira_enabled(_boot_config)

    # Check if /check_notifications was requested — only consume the signal
    # if at least one provider is enabled, otherwise leave it for the next
    # iteration where config may have changed (avoids silently dropping it).
    from app.loop_manager import _consume_check_notifications_signal
    force_notif_check = False
    if github_enabled or jira_enabled:
        force_notif_check = _consume_check_notifications_signal(koan_root)

    # Check GitHub notifications before planning (converts @mentions to missions
    # so plan_iteration() sees them immediately instead of waiting for sleep)
    gh_missions = 0
    if github_enabled:
        if is_first_iteration:
            _run._notify_raw(instance, "🔍 Scanning GitHub notifications (cold start, may take ~1 min)...")
        from app.loop_manager import (
            process_github_notifications,
            was_github_notification_check_throttled,
        )
        try:
            gh_missions = process_github_notifications(koan_root, instance, force=force_notif_check)
            if gh_missions > 0:
                log("github", f"Pre-iteration: {gh_missions} mission(s) created from GitHub notifications")
            elif not was_github_notification_check_throttled():
                log("koan", "No new GitHub notifications")
        except Exception as e:
            log("error", f"Pre-iteration GitHub notification check failed: {e}")

    # Check Jira notifications before planning (converts @mentions to missions
    # so plan_iteration() sees them immediately instead of waiting for sleep)
    jira_missions = 0
    if jira_enabled:
        # One first-iteration banner that combines the GitHub roll-up (when
        # applicable) with the cold-start latency hint. Avoids the prior
        # double-message ("🔍 Scanning Jira..." immediately followed by
        # "📋 GitHub: ... Scanning Jira...") that said the same thing twice.
        if is_first_iteration:
            cold = " (cold start, may take ~1 min)"
            if github_enabled and gh_missions > 0:
                _run._notify_raw(instance, f"📋 GitHub: {gh_missions} new mission(s) queued. Scanning Jira{cold}...")
            elif is_boot_iteration and github_enabled:
                _run._notify_raw(instance, f"📋 GitHub: scanned, no new missions. Scanning Jira{cold}...")
            else:
                # Boot without GitHub, or resume from pause: emit a single
                # cold-start banner so the human sees Jira IS being scanned.
                _run._notify_raw(instance, f"🔍 Scanning Jira notifications{cold}...")
        from app.loop_manager import (
            process_jira_notifications,
            was_jira_notification_check_throttled,
        )
        try:
            jira_missions = process_jira_notifications(koan_root, instance, force=force_notif_check)
            if jira_missions > 0:
                log("jira", f"Pre-iteration: {jira_missions} mission(s) created from Jira notifications")
            elif not was_jira_notification_check_throttled():
                log("koan", "No new Jira notifications")
        except Exception as e:
            log("error", f"Pre-iteration Jira notification check failed: {e}")

    if is_first_iteration:
        if jira_enabled and jira_missions > 0:
            _run._notify_raw(instance, f"🎯 Jira: {jira_missions} new mission(s) queued. Picking first mission from queue...")
        elif gh_missions > 0:
            _run._notify_raw(instance, f"🎯 GitHub: {gh_missions} new mission(s) queued. Picking first mission from queue...")
        elif is_boot_iteration:
            # Empty-state message: only at actual boot. Suppress on resume to
            # avoid spamming the human after every /pause+/resume or auto-resume.
            _run._notify_raw(instance, "🎯 Notifications clear. Picking first mission from queue...")

    # Startup update hint: surface upstream commits to the user (48 h throttled)
    if is_boot_iteration:
        try:
            from app.update_hint import maybe_send_update_hint
            maybe_send_update_hint(instance, koan_root)
        except Exception as e:
            log("error", f"Update hint check failed: {e}")

    # Plan iteration (delegated to iteration_manager)
    log("koan", "Planning iteration...")
    last_project = _run._read_current_project(koan_root)
    plan = plan_iteration(
        instance_dir=instance,
        koan_root=koan_root,
        run_num=run_num,
        count=count,
        projects=projects,
        last_project=last_project,
    )

    # --- Iteration decision summary (always visible in logs) ---
    log("koan", f"Iteration plan: action={plan['action']}, "
        f"project={plan['project_name']}, mode={plan['autonomous_mode']}, "
        f"budget={plan['available_pct']}%"
        f"{', mission=' + plan['mission_title'][:60] if plan['mission_title'] else ''}")
    if plan.get("error"):
        log("error", f"Iteration plan error: {plan['error']}")
    if plan.get("tracker_error"):
        log("error", f"Usage tracker broken: {plan['tracker_error']} — hard-capped to review mode")
        _run._notify(instance, f"⚠️ Budget tracker error: {plan['tracker_error']} — running in review-only mode until fixed")

    # Display usage — skip for idle-wait iterations (nothing to spend on)
    _IDLE_ACTIONS = {"exploration_wait", "passive_wait", "focus_wait",
                     "schedule_wait", "pr_limit_wait", "branch_saturated_wait"}
    if plan["action"] not in _IDLE_ACTIONS:
        log("quota", "Usage (token estimate — may differ from real API quota):")
        if plan["display_lines"]:
            for line in plan["display_lines"]:
                log("quota", f"  {line}")
        else:
            log("quota", "  [No usage data available - using fallback mode]")
        if plan.get("cost_today", 0.0) > 0:
            log("quota", f"  Cost today: ${plan['cost_today']:.2f}")
        log("quota", f"  Safety margin: 10% → Available: {plan['available_pct']}%")

    # Log recurring injections
    for line in plan.get("recurring_injected", []):
        log("mission", line)

    # --- Handle special actions ---
    action = plan["action"]
    project_name = plan["project_name"]
    project_path = plan["project_path"]

    if action == "error":
        mission_title = plan.get("mission_title", "")
        log("error", mission_title if not plan.get("error") else plan["error"])
        # Move the mission to Failed so it doesn't block the queue.
        # Without this, the same mission gets picked every iteration,
        # causing a retry loop until MAX_CONSECUTIVE_ERRORS triggers pause.
        if mission_title:
            _run._update_mission_in_file(instance, mission_title, failed=True)
            _fail_icon = "🚦" if _run._is_ci_check_mission(mission_title) else "❌"
            _run._notify(instance, f"{_fail_icon} Mission failed: {plan.get('error', mission_title)}")
            _run._commit_instance(instance)
        else:
            _run._notify(instance, f"⚠️ Iteration error: {plan.get('error', 'Unknown error')}")
        return False  # error handling — not productive

    if action == "contemplative":
        _run._handle_contemplative(plan, run_num, max_runs, koan_root, instance, interval)
        return True  # contemplative sessions consume API budget

    # Idle wait actions — all follow the same sleep-and-check pattern
    _IDLE_WAIT_CONFIG = {
        "passive_wait": lambda p: (
            f"Passive mode — read-only, waiting for /active ({p.get('passive_remaining', 'indefinite')})",
            f"👁️ Passive — read-only ({p.get('passive_remaining', 'indefinite')})",
        ),
        "focus_wait": lambda p: (
            f"Focus mode active ({p.get('focus_remaining', 'permanent')}) — waiting for missions",
            f"Focus mode — waiting for missions ({p.get('focus_remaining', 'permanent')})",
        ),
        "schedule_wait": lambda _: (
            "Work hours active — waiting for missions (exploration suppressed)",
            f"Work hours — waiting for missions ({time.strftime('%H:%M')})",
        ),
        "exploration_wait": lambda p: (
            p.get("decision_reason") or "All projects have exploration disabled — waiting for missions",
            f"Exploration disabled — waiting for missions ({time.strftime('%H:%M')})",
        ),
        "pr_limit_wait": lambda p: (
            p.get("decision_reason") or "PR limit reached for all projects — waiting for reviews",
            f"PR limit reached — waiting for reviews ({time.strftime('%H:%M')})",
        ),
        "branch_saturated_wait": lambda p: (
            p.get("decision_reason") or "Project branch-saturated — waiting for reviews/merges",
            f"Branch-saturated — waiting ({time.strftime('%H:%M')})",
        ),
    }
    if action in _IDLE_WAIT_CONFIG:
        global _last_idle_msg
        log_msg, status_msg = _IDLE_WAIT_CONFIG[action](plan)
        if log_msg != _last_idle_msg:
            log("koan", log_msg)
            _last_idle_msg = log_msg
        _run.set_status(koan_root, status_msg)
        idle_interval = _run._resolve_idle_wait_interval(
            interval, github_enabled, jira_enabled,
        )
        # branch_saturated_wait: the pending missions ARE the blocker
        # (the picked mission's project is over its PR limit), so waking
        # on pending missions would just tight-loop back into the same
        # blocked state. Wait the full interval for PR count to change.
        # passive_wait: passive mode blocks all execution, so waking on
        # a pending mission tight-loops (logs flood in make logs).
        wake_on_mission = action not in ("branch_saturated_wait", "passive_wait")
        with _run.protected_phase(status_msg):
            wake = interruptible_sleep(
                idle_interval, koan_root, instance,
                wake_on_mission=wake_on_mission,
            )
        if wake == "mission":
            _last_idle_msg = ""
            log("koan", f"New mission detected during {action} — waking up")
        # branch_saturated_wait is a human-unblock state (review PRs),
        # not an idle state — don't accumulate toward auto-pause.
        if action == "branch_saturated_wait":
            return False  # blocked on external action — not idle, not productive
        return "idle"  # idle wait — not productive, trackable

    if action == "wait_pause":
        _run._handle_wait_pause(plan, count, koan_root, instance)
        return False  # budget exhausted — not productive

    # --- Pre-flight quota check ---
    if action in ("mission", "autonomous"):
        log("koan", "Running pre-flight quota check...")
        if _run._run_preflight_check(plan, koan_root, instance, count):
            return False  # quota exhausted pre-flight — not productive
        log("koan", "Pre-flight OK — quota available")

    # --- Execute mission or autonomous run ---
    mission_title = plan["mission_title"]
    autonomous_mode = plan["autonomous_mode"]
    focus_area = plan["focus_area"]
    available_pct = plan["available_pct"]
    mission_tier = plan.get("mission_tier")  # complexity tier (may be None)

    # --- Dedup guard ---
    if mission_title:
        log("koan", "Checking mission dedup history...")
        try:
            from app.mission_history import should_skip_mission
            if should_skip_mission(instance, mission_title, max_executions=3):
                log("mission", f"Skipping repeated mission (3+ attempts): {mission_title[:60]}")
                moved = _run._update_mission_in_file(
                    instance, mission_title, failed=True,
                    cause_tag="repeated-3x",
                )
                if moved:
                    _run._notify(instance, f"⚠️ Mission ran 3+ times without clearing, moved to Failed: {mission_title[:60]}")
                else:
                    # The mission could not be matched in missions.md, so it
                    # stays in Pending and would be re-picked forever. Surface
                    # this loudly — a silent retry loop is the worst outcome.
                    log("error", f"Repeated mission could not be removed from queue: {mission_title[:80]}")
                    _run._notify(instance, (
                        f"🛑 Mission ran 3+ times but could NOT be removed from the queue "
                        f"(text mismatch in missions.md). It will keep being retried until you "
                        f"edit/cancel it manually: {mission_title[:80]}"
                    ))
                _run._commit_instance(instance)
                return False  # dedup skip — not productive
        except Exception as e:
            log("warning", f"Dedup guard error (proceeding anyway): {e}")
            # Don't skip — running a mission once extra is cheaper than
            # silently dropping it every iteration.

    # Set project state
    from app.signals import PROJECT_FILE
    atomic_write(Path(koan_root, PROJECT_FILE), project_name)
    os.environ["KOAN_CURRENT_PROJECT"] = project_name
    os.environ["KOAN_CURRENT_PROJECT_PATH"] = project_path

    log("project", bold_green(f">>> Current project: {project_name}") + f" ({project_path})")

    # --- Prepare project git state ---
    # Org-wide missions run at the workspace root (which is not itself a git
    # repo) and iterate over every repo themselves, handling each repo's git
    # branch/PR work inside the mission. Engine-level branch preparation would
    # fail there, so skip it for the org-wide sentinel.
    from app.constants import ORG_WIDE_PROJECT
    is_org_wide = project_name == ORG_WIDE_PROJECT
    if is_org_wide:
        log("git", f"Org-wide mission — running at workspace root ({project_path}); "
                    "skipping branch prep (mission manages git per repo)")
    else:
        from app.git_prep import prepare_project_branch
        try:
            prep = prepare_project_branch(project_path, project_name, koan_root)
            if prep.stashed:
                log("git", f"Stashed uncommitted changes in {project_name}")
            if not prep.success:
                log("error", f"Git prep failed for {project_name}: {prep.error}")
                if mission_title:
                    _run._update_mission_in_file(instance, mission_title, failed=True)
                    _gp_icon = "🚦" if _run._is_ci_check_mission(mission_title) else "❌"
                    _run._notify(instance, f"{_gp_icon} [{project_name}] Git prep failed, aborting mission: {mission_title[:60]}")
                return False  # abort — branch state is unreliable
            else:
                log("git", f"Ready on {prep.base_branch} from {prep.remote_used}")
        except Exception as e:
            log("error", f"Git prep error for {project_name}: {e}\n{traceback.format_exc()}")
            if mission_title:
                _run._update_mission_in_file(instance, mission_title, failed=True)
                _gp_icon = "🚦" if _run._is_ci_check_mission(mission_title) else "❌"
                _run._notify(instance, f"{_gp_icon} [{project_name}] Git prep error, aborting mission: {mission_title[:60]}")
            return False  # abort — branch state is unreliable

    # --- Mark mission as In Progress ---
    # Save the original title before skill dispatch may translate it.
    # _finalize_mission must use the original title because that's the
    # needle recorded in missions.md "In Progress" section.
    original_mission_title = mission_title
    if mission_title:
        if not _run._start_mission_in_file(instance, mission_title, project_name):
            log("warning", f"start_mission transition failed for '{mission_title[:60]}' — "
                "mission may still be in Pending; aborting this run to avoid duplicate execution.")
            return False

    # --- Create structured checkpoint for recovery ---
    if mission_title:
        try:
            from app.checkpoint_manager import create_checkpoint
            create_checkpoint(instance, mission_title, project_name, run_num)
        except Exception as e:
            log("error", f"Checkpoint creation failed (non-blocking): {e}")

    # --- Check for skill-dispatched mission ---
    if mission_title:
        handled, mission_title = _run._handle_skill_dispatch(
            mission_title, project_name, project_path, koan_root,
            instance, run_num, max_runs, autonomous_mode, interval,
            mission_tier=mission_tier or "",
        )
        if handled:
            return True  # skill dispatch — productive

    # Lifecycle notification
    if mission_title:
        log("mission", "Decision: MISSION mode (assigned)")
        log("mission", f"  Mission: {mission_title}")
        log("mission", f"  Project: {project_name}")
        _run._notify(instance, f"🚀 [{project_name}] Run {run_num}/{max_runs} — Starting: {mission_title}")
    else:
        mode_upper = autonomous_mode.upper()
        log("mission", f"Decision: {mode_upper} mode (estimated cost: 5.0% session)")
        log("mission", f"  Reason: {plan['decision_reason']}")
        log("mission", f"  Project: {project_name}")
        log("mission", f"  Focus: {focus_area}")
        _run._notify(instance, f"🚀 [{project_name}] Run {run_num}/{max_runs} — Autonomous: {autonomous_mode} mode")

    # --- Fire pre-mission hook ---
    try:
        from app.hooks import fire_hook
        fire_hook(
            "pre_mission",
            instance_dir=instance,
            project_name=project_name,
            project_path=project_path,
            mission_title=mission_title,
            autonomous_mode=autonomous_mode,
            run_num=run_num,
        )
    except Exception as e:
        print(f"[hooks] pre_mission hook error: {e}", file=sys.stderr)

    # --- Generate mission spec for complex missions ---
    spec_content = ""
    if mission_title and autonomous_mode not in ("review", "wait"):
        try:
            from app.mission_complexity import is_complex_mission
            if is_complex_mission(mission_title):
                log("spec", "Complex mission detected — generating spec")
                from app.spec_generator import generate_spec, save_spec
                spec_content = generate_spec(project_path, mission_title, instance) or ""
                if spec_content:
                    spec_path = save_spec(instance, mission_title, spec_content)
                    if spec_path:
                        log("spec", f"Spec saved to {spec_path}")
                    else:
                        log("spec", "Spec generated but save failed")
                else:
                    log("spec", "Spec generation returned empty — proceeding without spec")
        except Exception as e:
            log("error", f"Spec generation error (non-blocking): {e}")

    # --- Devcontainer-aware path computation (must be before prompt build) ---
    # Both the config flag AND a present .devcontainer config must be true
    # before we switch any paths to container-side — otherwise the prompt and
    # the actual execution environment would disagree.
    from app import devcontainer as _dc
    from app.projects_config import load_projects_config, get_project_devcontainer_enabled
    _dc_projects_config = load_projects_config(koan_root)
    _dc_configured = bool(_dc_projects_config and get_project_devcontainer_enabled(_dc_projects_config, project_name))
    _dc_present, _dc_workspace_path = (
        _dc.get_devcontainer_config(project_path) if _dc_configured else (False, project_path)
    )
    if _dc_configured and not _dc_present:
        log("warning",
            f"[devcontainer] devcontainer: true set for '{project_name}' "
            f"but no .devcontainer/devcontainer.json found — running on host")
    if _dc_present:
        _koan_tmp = Path(koan_root) / "devcontainer-tmp"
        _koan_tmp.mkdir(exist_ok=True)
        _host_tmp_dir: Optional[str] = str(_koan_tmp)
        _container_tmp_dir: Optional[str] = _dc.CONTAINER_TMP_DIR
        _prompt_instance = _dc.CONTAINER_INSTANCE_DIR
        _prompt_project_path = _dc_workspace_path
    else:
        _host_tmp_dir = None
        _container_tmp_dir = None
        _prompt_instance = instance
        _prompt_project_path = project_path

    # Build prompt (split into system/user for prompt caching)
    from app.prompt_builder import build_agent_prompt_parts
    system_prompt, prompt = build_agent_prompt_parts(
        instance=_prompt_instance,
        project_name=project_name,
        project_path=_prompt_project_path,
        run_num=run_num,
        max_runs=max_runs,
        autonomous_mode=autonomous_mode or "implement",
        focus_area=focus_area or "General autonomous work",
        available_pct=available_pct or 50,
        mission_title=mission_title,
        spec_content=spec_content,
    )

    # Create pending.md
    from app.loop_manager import create_pending_file
    try:
        create_pending_file(
            instance_dir=instance,
            project_name=project_name,
            run_num=run_num,
            max_runs=max_runs,
            autonomous_mode=autonomous_mode or "implement",
            mission_title=mission_title,
        )
    except Exception as e:
        log("error", f"Failed to create pending.md: {e}")

    # Execute Claude
    log("koan", "Building CLI command and launching provider...")
    if mission_title:
        _run.set_status(koan_root, f"Run {run_num}/{max_runs} — executing mission on {project_name}")
    else:
        _run.set_status(koan_root, f"Run {run_num}/{max_runs} — {autonomous_mode.upper()} on {project_name}")

    mission_start = int(time.time())
    from app.utils import koan_tmp_dir
    fd_out, stdout_file = tempfile.mkstemp(prefix="koan-out-", dir=koan_tmp_dir())
    os.close(fd_out)
    fd_err, stderr_file = tempfile.mkstemp(prefix="koan-err-", dir=koan_tmp_dir())
    os.close(fd_err)
    claude_exit = 1  # default to failure; overwritten on successful execution
    provider_name = ""
    provider_label = "Provider"
    plugin_dir = None  # generated plugin dir for Skill tool (cleaned up in finally)
    cmd_cleanup_paths: List[str] = []  # temp files created by build_mission_command
    _dc_container_id = ""  # set inside try if devcontainer is used; referenced in finally
    try:
        provider_name, provider_label = _run._provider_identity()
        # Build CLI command (provider-agnostic with per-project overrides)
        from app.mission_runner import build_mission_command
        from app.debug import debug_log as _debug_log
        if provider_name == "codex":
            try:
                from app.config import get_skip_permissions
                _codex_full_access = get_skip_permissions()
            except Exception as e:
                _codex_full_access = False
                _debug_log(f"[run] codex skip_permissions check failed: {e}")
            _mission_mode = (autonomous_mode or "implement").lower()
            if not _codex_full_access and _mission_mode in {"implement", "deep"}:
                log(
                    "warning",
                    "Codex workspace-write sandbox may make .git read-only; "
                    "branch, commit, push, and PR creation can fail. "
                    "Set skip_permissions: true when Koan runs in a trusted "
                    "external sandbox and Codex should use git directly.",
                )

        # Generate plugin directory so Claude CLI can discover Kōan skills
        plugin_dirs = None
        try:
            from app.plugin_generator import generate_plugin_dir, cleanup_plugin_dir
            from app.skills import build_registry
            extra_dirs = []
            # Include project-local skills (<project>/.claude/skills/)
            project_skills = Path(project_path) / ".claude" / "skills"
            if project_skills.is_dir():
                extra_dirs.append(project_skills)
            instance_skills = Path(instance) / "skills"
            if instance_skills.is_dir():
                extra_dirs.append(instance_skills)
            # Include user-installed Claude Code skills (~/.claude/skills/)
            user_skills = Path.home() / ".claude" / "skills"
            if user_skills.is_dir():
                extra_dirs.append(user_skills)
            registry = build_registry(extra_dirs=extra_dirs or None)
            if registry.list_by_audience("agent", "command", "hybrid"):
                # In devcontainer mode, generate plugin dir inside the dedicated
                # tmp mount so --plugin-dir paths are accessible in the container.
                dc_base = Path(_host_tmp_dir) if _host_tmp_dir else None
                plugin_dir = generate_plugin_dir(registry, base_dir=dc_base)
                plugin_dirs = [str(plugin_dir)]
        except Exception as e:
            _debug_log(f"[run] plugin dir generation skipped: {e}")

        cmd, cmd_cleanup_paths = build_mission_command(
            prompt=prompt,
            autonomous_mode=autonomous_mode,
            extra_flags="",
            project_name=project_name,
            plugin_dirs=plugin_dirs,
            system_prompt=system_prompt,
            tier=mission_tier,
            system_prompt_dir=_host_tmp_dir,
            system_prompt_container_dir=_container_tmp_dir,
        )

        cmd_display = [c[:100] + '...' if len(c) > 100 else c for c in cmd[:6]]
        _debug_log(f"[run] cli: cmd={' '.join(cmd_display)}... cwd={project_path}")

        # --- Devcontainer mode ---
        if _dc_present:
            try:
                _dc_container_id = _dc.prepare_devcontainer(
                    project_path,
                    provider_name=provider_name,
                    instance_path=instance,
                    koan_tmp_path=_host_tmp_dir or "",
                )
            except RuntimeError as e:
                log("error", f"[devcontainer] setup failed for '{project_name}': {e}")
                if original_mission_title:
                    _run._update_mission_in_file(instance, original_mission_title, failed=True)
                    _run._notify(instance, f"❌ [{project_name}] Devcontainer setup failed: {e}")
                return False
            cmd = _dc.wrap_command(
                cmd, project_path,
                host_tmp_dir=_host_tmp_dir or "",
                container_tmp_dir=_container_tmp_dir or "",
            )

        # Capture git HEAD before execution for retry safety check
        pre_head = _run._get_git_head(project_path)

        # Snapshot core files before execution for integrity check
        from app.core_files import snapshot_core_files, check_core_files, log_integrity_warnings
        core_snapshot = snapshot_core_files(koan_root, project_path)

        claude_exit = _run.run_claude_task(
            cmd, stdout_file, stderr_file, cwd=project_path,
            instance_dir=instance, project_name=project_name, run_num=run_num,
        )

        _debug_log(f"[run] cli: exit_code={claude_exit}")
        elapsed_min = (int(time.time()) - mission_start) / 60
        log("koan", f"{provider_label} CLI finished (exit={claude_exit}, {elapsed_min:.1f}min)")

        # --- Mission retry on transient CLI errors ---
        # One retry for missions, zero for autonomous (they're lower-priority).
        # Only retry if HEAD didn't move (no commits produced).
        if claude_exit != 0:
            claude_exit, stdout_file, stderr_file = _run._maybe_retry_mission(
                claude_exit=claude_exit,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                cmd=cmd,
                project_path=project_path,
                pre_head=pre_head,
                instance=instance,
                project_name=project_name,
                run_num=run_num,
                has_mission=bool(mission_title),
                provider_name=provider_name,
            )

        # --- JSON success override ---
        # Claude CLI can return non-zero even when the session JSON shows
        # success (is_error=false).  Override the exit code so the
        # post-mission pipeline (verification, reflection, auto-merge)
        # is not skipped and the notification shows ✅ instead of ❌.
        # NEVER override after a watchdog kill or user abort — partial
        # JSON output from a killed process is not trustworthy (#1254).
        if claude_exit != 0 and not _run._last_mission_timed_out and not _run._last_mission_aborted:
            from app.mission_runner import check_json_success
            if check_json_success(stdout_file):
                log("koan", f"CLI exited {claude_exit} but JSON output indicates success — overriding to 0")
                claude_exit = 0

        # Verify core files survived the mission (after retry, so result is final)
        log("koan", "Running core file integrity check...")
        integrity_warnings = check_core_files(koan_root, core_snapshot, project_path)
        if integrity_warnings:
            from app.core_files import recover_project_files
            missing = core_snapshot - snapshot_core_files(koan_root, project_path)
            recovered, unrecoverable = recover_project_files(missing, project_path)
            if recovered:
                log("core_files", f"Auto-recovered {len(recovered)} file(s): {', '.join(recovered)}")
            if unrecoverable:
                log_integrity_warnings(unrecoverable)
                log("error", f"Core file integrity check failed: {len(unrecoverable)} file(s) unrecoverable")
                claude_exit = 1

        # Parse and display output
        try:
            from app.mission_runner import parse_claude_output
            with open(stdout_file) as f:
                raw = f.read()
            text = parse_claude_output(raw)
            print(text)
        except Exception as e:
            try:
                with open(stdout_file) as f:
                    print(f.read())
            except Exception as e2:
                log("error", f"Failed to read CLI output: {e}, {e2}")
        _reset_terminal()

        # --- Update checkpoint with branch/progress as early as possible ---
        # Done before auth/quota checks so progress is captured even on early returns.
        if original_mission_title:
            try:
                from app.checkpoint_manager import (
                    update_checkpoint, update_from_pending, update_from_stdout,
                )
                from app.git_sync import run_git as _cp_run_git
                _cp_branch = _cp_run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
                if _cp_branch:
                    update_checkpoint(instance, original_mission_title, branch=_cp_branch)
                update_from_pending(instance, original_mission_title)
                with suppress_logged(log, "warning", "Checkpoint stdout read failed", OSError):
                    _cp_stdout = Path(stdout_file).read_text(errors="replace")
                    update_from_stdout(instance, original_mission_title, _cp_stdout)
            except Exception as e:
                log("error", f"Checkpoint update failed (non-blocking): {e}")

        # --- Auth / Quota error detection (before finalizing mission) ---
        if claude_exit != 0 and original_mission_title:
            try:
                _cli_stdout = Path(stdout_file).read_text()
            except OSError:
                _cli_stdout = ""
            try:
                _cli_stderr = Path(stderr_file).read_text()
            except OSError:
                _cli_stderr = ""
            if _run._classify_and_handle_cli_error(
                claude_exit, _cli_stdout, _cli_stderr,
                provider_name=provider_name,
                provider_label=provider_label,
                koan_root=koan_root,
                instance=instance,
                project_name=project_name,
                mission_title=original_mission_title,
                run_num=run_num,
                hqe_kwargs=dict(
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    exit_code=claude_exit,
                ),
            ):
                return True

        # Exit-0 quota probe — check all CLI outcomes before finalization.
        if original_mission_title:
            _exit0_hqe = dict(
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                exit_code=claude_exit,
            )
            if _run._probe_exit0_quota(
                provider_name=provider_name,
                provider_label=provider_label,
                koan_root=koan_root,
                instance=instance,
                mission_title=original_mission_title,
                run_num=run_num,
                hqe_kwargs=_exit0_hqe,
                project_name=project_name,
            ):
                return True

        # If mission was aborted, notify and skip heavy post-mission pipeline
        if _run._last_mission_aborted and original_mission_title:
            _run._finalize_mission(instance, original_mission_title, project_name, claude_exit)
            try:
                from app.checkpoint_manager import delete_checkpoint
                delete_checkpoint(instance, original_mission_title)
            except Exception as e:
                log("error", f"Checkpoint cleanup failed (non-blocking): {e}")
            log("koan", f"Mission aborted: {original_mission_title[:60]}")
            _run._notify(instance, f"⏭️ [{project_name}] Mission aborted: {original_mission_title[:60]}")
            return True  # count as productive so loop continues immediately

        # Post-mission pipeline
        log("koan", "Starting post-mission pipeline...")
        _status_prefix = f"Run {run_num}/{max_runs}"
        _run.set_status(koan_root, f"{_status_prefix} — finalizing")
        try:
            from app.mission_runner import run_post_mission
            from app.restart_manager import RESTART_EXIT_CODE
            post_result = run_post_mission(
                instance_dir=instance,
                project_name=project_name,
                project_path=project_path,
                run_num=run_num,
                exit_code=claude_exit,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                mission_title=mission_title,
                autonomous_mode=autonomous_mode or "implement",
                start_time=mission_start,
                status_callback=lambda step: _run.set_status(
                    koan_root, f"{_status_prefix} — {step}"
                ),
                mission_tier=mission_tier,
                provider_name=provider_name,
            )

            if post_result.get("pending_archived"):
                log("health", f"pending.md archived to journal ({provider_label} didn't clean up)")
            if post_result.get("auto_merge_branch"):
                log("git", f"Auto-merge checked for {post_result['auto_merge_branch']}")

            if post_result.get("quota_exhausted"):
                _run._handle_pipeline_quota_flag(
                    provider_label=provider_label,
                    koan_root=koan_root,
                    instance=instance,
                    mission_title=original_mission_title,
                    count=count,
                    quota_info=post_result.get("quota_info"),
                )
                return True  # ran Claude before quota hit — productive
        except Exception as e:
            log("error", f"Post-mission processing error: {e}\n{traceback.format_exc()}")

        # Complete/fail mission in missions.md after quota handling has had a
        # chance to requeue transient quota failures.
        if original_mission_title:
            _run._finalize_mission(instance, original_mission_title, project_name, claude_exit)

        # --- Clean up checkpoint after mission finalization ---
        # Delete on both success and failure to prevent orphaned checkpoint files.
        # Recovery only matters for in-progress missions (crash); once finalized,
        # the checkpoint is no longer needed.
        if original_mission_title:
            try:
                from app.checkpoint_manager import delete_checkpoint
                delete_checkpoint(instance, original_mission_title)
            except Exception as e:
                log("error", f"Checkpoint cleanup failed (non-blocking): {e}")
    finally:
        if _dc_container_id:
            log("devcontainer", f"Stopping container {_dc_container_id[:12]} after mission")
            _dc.stop_container(_dc_container_id)
        _run._cleanup_temp(stdout_file, stderr_file)
        if cmd_cleanup_paths:
            try:
                from app.provider import cleanup_managed_paths
                cleanup_managed_paths(cmd_cleanup_paths)
            except Exception as e:
                print(f"[run] sysprompt cleanup error: {e}", file=sys.stderr)
        if plugin_dir:
            try:
                from app.plugin_generator import cleanup_plugin_dir
                cleanup_plugin_dir(plugin_dir)
            except Exception as e:
                print(f"[run] plugin cleanup error: {e}", file=sys.stderr)

    # Report result — always notify on completion (success or failure)
    if claude_exit == 0:
        log("mission", f"Run {run_num}/{max_runs} — [{project_name}] completed successfully")
    _run._notify_mission_end(
        instance, project_name, run_num, max_runs,
        claude_exit, mission_title,
    )

    # Commit instance
    _run._commit_instance(instance)

    # Periodic git sync
    if (count + 1) % git_sync_interval == 0:
        with _run.protected_phase("Git sync"):
            log("git", f"Periodic git sync (run {count + 1})...")
            from app.git_sync import GitSync
            for name, path in projects:
                try:
                    gs = GitSync(instance, name, path)
                    gs.sync_and_report()
                except Exception as e:
                    log("error", f"Periodic git sync failed for {name}: {e}")

    # Periodic auto-update check
    try:
        from app.auto_update import is_auto_update_enabled, get_check_interval
        from app.restart_manager import RESTART_EXIT_CODE
        if is_auto_update_enabled() and (count + 1) % get_check_interval() == 0:
            from app.auto_update import perform_auto_update
            updated = perform_auto_update(koan_root, instance)
            if updated:
                log("update", "Auto-update triggered restart.")
                sys.exit(RESTART_EXIT_CODE)
    except Exception as e:
        log("error", f"Periodic auto-update check failed: {e}")

    # Max runs check
    if count + 1 >= max_runs:
        from app.config import get_auto_pause
        if get_auto_pause():
            log("koan", f"Max runs ({max_runs}) reached. Running evening ritual before pause.")
            with _run.protected_phase("Evening ritual"):
                try:
                    from app.rituals import run_ritual
                    run_ritual("evening", Path(instance))
                except Exception as e:
                    log("error", f"Evening ritual failed: {e}")
            log("pause", "Entering pause mode (auto-resume in 5h).")
            from app.pause_manager import create_pause
            create_pause(koan_root, "max_runs")
            _run._notify(instance, (
                f"⏸️ Kōan paused: {max_runs} runs completed. "
                "Auto-resume in 5h or use /resume to restart."
            ))
            return True  # completed final productive run
        else:
            log("koan", f"Max runs ({max_runs}) reached but auto_pause disabled — continuing.")

    # Sleep between runs (skip if pending missions)
    _run._sleep_between_runs(koan_root, instance, interval, run_num, max_runs)

    return True  # productive iteration completed
