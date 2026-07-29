"""Repo-config-driven shell hooks around missions (``pre_hooks``/``post_hooks``).

A target repo's ``.koan/config.yaml`` may declare shell commands to run before
(``pre_hooks``) and after (``post_hooks``) a mission — see
``project_koan.get_mission_hooks`` for the schema/resolver and
``specs/components/agent-loop.md`` → "Mission hooks" for the contract.

This module owns the *execution* half: the operator opt-in gate and the
best-effort subprocess executor. It is deliberately **separate** from
``hooks.py`` (operator-authored Python from the trusted ``instance/`` tree):
here we run **shell from a repo-controlled file**, a remote-code-execution
surface, so nothing runs unless the operator explicitly opts in.

Security posture:
    - Default off. ``hooks_enabled()`` gates every entry point.
    - ``shell=True`` is intentional (operators want pipelines/``&&``) and is
      acceptable *only* because execution is already operator-authorized.
    - Best-effort: a failing/timing-out/unlaunchable command is logged and
      swallowed — it never aborts the mission, blocks later commands, or raises.
"""
import contextlib
import os
import signal
import subprocess
from typing import List, Optional

from app.run_log import log_safe as _log

# Per-command wall-clock cap (seconds). A hung hook cannot stall the loop
# indefinitely; on timeout the command is terminated and the run continues.
MISSION_HOOK_TIMEOUT = 300

# Bound on captured stdout/stderr echoed per command, so a chatty hook cannot
# flood the run log.
_MAX_HOOK_LOG_CHARS = 4000

# Remembered so the "skipped (not enabled)" diagnostic is logged at most once
# per process rather than on every mission.
_skip_logged = False


def hooks_enabled(project_name: str) -> bool:
    """Whether mission hooks may run for ``project_name``.

    Per-project override (``projects.yaml`` ``mission_hooks:``) wins when set;
    otherwise the global operator opt-in (``mission_hooks.enabled`` in
    ``instance/config.yaml``, default False). Fail-safe: any error ⇒ False.
    """
    try:
        from app.projects_config import get_project_mission_hooks
        override = get_project_mission_hooks(project_name)
        if override is not None:
            return override
        from app.config import is_mission_hooks_enabled
        return is_mission_hooks_enabled()
    except Exception as e:  # never let a config error enable/crash hooks
        _log("error", f"[mission_hooks] enablement check failed: {e}")
        return False


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > _MAX_HOOK_LOG_CHARS:
        return text[:_MAX_HOOK_LOG_CHARS] + " …[truncated]"
    return text


def _run_commands(
    commands: List[str],
    project_path: str,
    mission_type: str,
    *,
    status: Optional[str] = None,
) -> None:
    """Run each command in order, error-isolated. Never raises.

    ``KOAN_MISSION_TYPE`` is always exported; ``KOAN_MISSION_STATUS`` is exported
    only when ``status`` is given (post-hooks).
    """
    env = {**os.environ, "KOAN_MISSION_TYPE": mission_type or ""}
    if status is not None:
        env["KOAN_MISSION_STATUS"] = status
    total = len(commands)
    phase = "post" if status is not None else "pre"
    for idx, command in enumerate(commands, start=1):
        label = f"[mission_hooks] {phase} {mission_type or '-'} {idx}/{total}"
        proc = None
        try:
            # start_new_session=True puts the shell (and every child it spawns)
            # in its own process group, so a timeout can reap the whole tree via
            # killpg — otherwise subprocess.run's timeout kills only the shell
            # and leaves orphaned children running past the "bounded" window.
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=project_path or None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=MISSION_HOOK_TIMEOUT)
            out = _truncate((stdout or "") + (stderr or ""))
            _log("mission", f"{label} exit={proc.returncode}: {command}")
            if out:
                _log("mission", f"{label} output:\n{out}")
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            _log(
                "error",
                f"{label} timed out after {MISSION_HOOK_TIMEOUT}s "
                f"(process group killed): {command}",
            )
        except Exception as e:  # launch failure, bad cwd, etc.
            _kill_process_group(proc)
            _log("error", f"{label} failed to run ({e}): {command}")


def _kill_process_group(proc: "Optional[subprocess.Popen]") -> None:
    """Best-effort SIGKILL of a timed-out/failed hook's whole process group.

    The hook shell was launched with ``start_new_session=True``, so its pid is
    also its process-group id; killing the group reaps any children it spawned.
    Reaps the shell afterwards so no zombie lingers. Never raises.
    """
    if proc is None or proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # Group already gone, or platform without killpg — fall back to the
        # direct child so we at least don't leak the shell itself.
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def run_pre_hooks(project_path: str, project_name: str, mission_type: str) -> None:
    """Run resolved ``pre_hooks`` for the mission. Gated + best-effort."""
    _run_phase(project_path, project_name, mission_type, "pre", status=None)


def run_post_hooks(
    project_path: str, project_name: str, mission_type: str, success: bool,
) -> None:
    """Run resolved ``post_hooks`` for the mission (both success and failure).

    Exports ``KOAN_MISSION_STATUS=success|failure`` to each command. Gated +
    best-effort.
    """
    status = "success" if success else "failure"
    _run_phase(project_path, project_name, mission_type, "post", status=status)


def _run_phase(
    project_path: str,
    project_name: str,
    mission_type: str,
    phase: str,
    *,
    status: Optional[str],
) -> None:
    global _skip_logged
    try:
        if not hooks_enabled(project_name):
            if not _skip_logged:
                _log(
                    "mission",
                    "[mission_hooks] skipped (not enabled) — set "
                    "mission_hooks.enabled: true to run repo pre/post hooks",
                )
                _skip_logged = True
            return
        from app.project_koan import get_mission_hooks
        commands = get_mission_hooks(project_path, mission_type, phase)
        if not commands:
            return
        _run_commands(
            commands, project_path, mission_type, status=status,
        )
    except Exception as e:  # the whole subsystem is fire-and-forget
        _log("error", f"[mission_hooks] {phase} phase error (ignored): {e}")
