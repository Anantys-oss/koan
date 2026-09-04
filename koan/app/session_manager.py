"""Kōan — Session manager for parallel agent execution.

Manages parallel Claude Code sessions, each running in its own git worktree:
- Session dataclass: tracks individual session state
- SessionRegistry: persistent session tracking via sessions.json (fcntl-locked)
- spawn_session(): create worktree + start Claude subprocess in a mission scope
- poll_sessions(): check subprocess status, collect results, tear the scope down
- kill_session(): terminate a session, tear the scope down and clean up
- get_max_parallel_sessions(): read config

Every session is spawned through ``mission_scope.launch_scoped`` — the third
spawn path in Kōan, alongside ``run_claude_task`` and ``_run_skill_mission`` —
so a build daemon that detaches to PPID 1 is still contained by a cgroup rather
than escaping the mission's process group.

The registry file (instance/sessions.json) follows Koan's existing pattern
of file-based state with fcntl locks for cross-process safety.
"""

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.worktree_manager import (
    create_worktree,
    inject_worktree_claude_md,
    prune_worktrees,
    reap_foreign_worktrees,
    remove_worktree,
    setup_shared_deps,
)


# Default configuration
DEFAULT_MAX_PARALLEL = 1
MAX_PARALLEL_CAP = 5
SESSIONS_FILE = "sessions.json"


@dataclass
class Session:
    """Represents a single parallel agent session."""
    id: str
    mission_text: str
    project_name: str
    project_path: str
    worktree_path: str
    branch_name: str
    pid: int = 0
    status: str = "pending"  # pending | running | done | failed
    started_at: float = 0.0
    finished_at: float = 0.0
    exit_code: int = -1
    stdout_file: str = ""
    stderr_file: str = ""
    autonomous_mode: str = "implement"


@dataclass
class SessionResult:
    """Result from a completed session."""
    session: Session
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    # Cap phrase from this session's own ScopedProcess ("" when it fit). A
    # parallel session has no per-mission global to read: app.run's
    # _last_mission_memory_cap belongs to the sequential mission, and reading
    # it here would stamp a stale cap hit on a session that never came near
    # its own — while the session's real cap hit went unreported.
    memory_cap_detail: str = ""


class SessionRegistry:
    """Persistent session tracking via sessions.json with file locking.

    Thread-safe and process-safe via fcntl.flock.
    """

    def __init__(self, instance_dir: str):
        self.instance_dir = instance_dir
        self._path = Path(instance_dir) / SESSIONS_FILE
        self._lock = threading.Lock()

    def _lock_path(self) -> Path:
        return self._path.with_suffix(".lock")

    def _read_unlocked(self) -> Dict[str, dict]:
        """Read sessions.json without acquiring a lock (caller holds it)."""
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_unlocked(self, data: Dict[str, dict]):
        """Write sessions.json atomically (caller holds the file lock)."""
        from app.utils import atomic_write_json
        atomic_write_json(self._path, data, indent=2)

    def _read_locked(self) -> Dict[str, dict]:
        """Read sessions.json under a shared file lock."""
        if not self._path.exists():
            return {}
        try:
            with open(self._lock_path(), "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_SH)
                try:
                    return self._read_unlocked()
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
        except OSError:
            return {}

    def _mutate(self, fn):
        """Run fn(data) → data under exclusive file lock (atomic read-modify-write)."""
        with self._lock:
            with open(self._lock_path(), "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)
                try:
                    data = self._read_unlocked()
                    data = fn(data)
                    self._write_unlocked(data)
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)

    def register(self, session: Session):
        """Register a new session."""
        def _add(data):
            data[session.id] = asdict(session)
            return data
        self._mutate(_add)

    def update(self, session: Session):
        """Update an existing session."""
        def _upd(data):
            data[session.id] = asdict(session)
            return data
        self._mutate(_upd)

    def remove(self, session_id: str):
        """Remove a session from the registry."""
        def _rm(data):
            data.pop(session_id, None)
            return data
        self._mutate(_rm)

    def get(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        data = self._read_locked()
        entry = data.get(session_id)
        if entry:
            return _dict_to_session(entry)
        return None

    def get_all(self) -> List[Session]:
        """Get all registered sessions."""
        data = self._read_locked()
        return [_dict_to_session(v) for v in data.values()]

    def get_active(self) -> List[Session]:
        """Get sessions with status 'running'."""
        return [s for s in self.get_all() if s.status == "running"]

    def get_by_project(self, project_name: str) -> List[Session]:
        """Get active sessions for a specific project (case-insensitive)."""
        lower = project_name.lower()
        return [
            s for s in self.get_active()
            if s.project_name.lower() == lower
        ]

    def clear_completed(self):
        """Remove all done/failed sessions from the registry."""
        def _clear(data):
            return {
                k: v for k, v in data.items()
                if v.get("status") in ("pending", "running")
            }
        self._mutate(_clear)


def _dict_to_session(d: dict) -> Session:
    """Convert a dict to a Session, ignoring unknown keys."""
    known = {f.name for f in Session.__dataclass_fields__.values()}
    filtered = {k: v for k, v in d.items() if k in known}
    return Session(**filtered)


def get_max_parallel_sessions() -> int:
    """Read max_parallel_sessions from config.yaml (default: 1, max: 5)."""
    try:
        from app.utils import load_config
        config = load_config()
        n = int(config.get("max_parallel_sessions", DEFAULT_MAX_PARALLEL))
        return max(1, min(n, MAX_PARALLEL_CAP))
    except Exception as e:
        print(f"[session_manager] config read error: {e}", file=sys.stderr)
        return DEFAULT_MAX_PARALLEL


def spawn_session(
    mission_text: str,
    project_name: str,
    project_path: str,
    instance_dir: str,
    registry: SessionRegistry,
    autonomous_mode: str = "implement",
    base_branch: str = "main",
    shared_deps: Optional[List[str]] = None,
) -> Session:
    """Create a worktree and start a Claude Code subprocess for a mission.

    Args:
        mission_text: The mission to execute.
        project_name: Project name.
        project_path: Path to the project repository.
        instance_dir: Path to the instance directory.
        registry: SessionRegistry for tracking.
        autonomous_mode: Mode for tool selection.
        base_branch: Branch to base the worktree on.
        shared_deps: Dependency dirs to symlink.

    Returns:
        Session with subprocess started and registered.
    """
    from app.mission_runner import build_mission_command
    from app.provider import get_provider_for_role

    # Create worktree
    wt = create_worktree(project_path, base_branch=base_branch)

    # Setup shared dependencies
    if shared_deps:
        setup_shared_deps(wt.path, project_path, shared_deps)

    # Inject mission context into worktree CLAUDE.md
    inject_worktree_claude_md(wt.path, mission_text)

    # Resolve the role's CLI provider (cli: section) once, so the command is
    # BUILT and EXECUTED under the same provider — otherwise popen_cli would
    # fall back to the global provider for stdin-rewrite and the invocation
    # lock, mismatching a cross-provider role (e.g. cli.mission: codex under a
    # global claude). Mirrors mission_executor._run_iteration.
    effective_role = "review_mode" if autonomous_mode == "review" else "mission"
    session_cli_provider = get_provider_for_role(effective_role, project_name)

    # Build CLI command
    try:
        from app.session_tracker import classify_mission_type
        _mission_type = classify_mission_type(mission_text)
    except ImportError as e:
        # Version mismatch / partial update — degrade to mode-only effort.
        # Log so a partial upgrade disabling effort pins is observable.
        print(
            f"[session_manager] classify_mission_type unavailable "
            f"(effort pin disabled): {e}",
            file=sys.stderr,
        )
        _mission_type = ""
    # classify_mission_type is pure regex (no I/O); any other exception is a
    # programming bug and is left to propagate rather than silently disabling
    # effort pins.
    cmd, cmd_cleanup_paths = build_mission_command(
        prompt=mission_text,
        autonomous_mode=autonomous_mode,
        project_name=project_name,
        provider_override=session_cli_provider,
        mission_type=_mission_type,
    )

    # Create temp files for stdout/stderr
    from app.utils import cleanup_mission_tmp_dir, create_mission_tmp_dir, koan_tmp_dir

    fd_out, stdout_file = tempfile.mkstemp(prefix=f"koan-session-{wt.session_id}-out-", dir=koan_tmp_dir())
    os.close(fd_out)
    fd_err, stderr_file = tempfile.mkstemp(prefix=f"koan-session-{wt.session_id}-err-", dir=koan_tmp_dir())
    os.close(fd_err)

    # Per-session TMPDIR, reaped in _session_cleanup (crash leftovers are
    # swept at startup by reap_stale_mission_tmp_dirs). Non-fatal on failure:
    # the child then inherits the parent's TMPDIR (prior behavior).
    session_tmp = None
    try:
        session_tmp = create_mission_tmp_dir(tag=wt.session_id)
    except OSError as e:
        print(f"[session_manager] session TMPDIR creation failed: {e}", file=sys.stderr)

    # Create session
    session = Session(
        id=wt.session_id,
        mission_text=mission_text,
        project_name=project_name,
        project_path=project_path,
        worktree_path=wt.path,
        branch_name=wt.branch,
        status="running",
        started_at=time.time(),
        stdout_file=stdout_file,
        stderr_file=stderr_file,
        autonomous_mode=autonomous_mode,
    )

    # Start subprocess — file handles must outlive the process
    from app import mission_scope
    from app.cli_exec import popen_cli

    out_f = None
    err_f = None
    # Bound before the try so the nested spawn below has a `nonlocal` binding
    # to assign into, and so the failure path can tell "never spawned" from
    # "spawned, and something holds the provider's invocation lock".
    cli_cleanup = None
    try:
        out_f = open(stdout_file, "w")  # noqa: SIM115
        err_f = open(stderr_file, "w")  # noqa: SIM115
        popen_kwargs = {}
        if session_tmp:
            from app.utils import pytest_addopts_with_basetemp
            child_env = {**os.environ, "TMPDIR": session_tmp}
            child_env["PYTEST_ADDOPTS"] = pytest_addopts_with_basetemp(
                child_env.get("PYTEST_ADDOPTS", ""), session_tmp
            )
            popen_kwargs["env"] = child_env

        def _spawn_in_scope(argv, launcher, **kwargs):
            # popen_cli owns the provider's prompt-file stdin and the
            # invocation lock, so the scope launcher is handed to it and
            # prefixed there — after the prompt rewrite, never before.
            nonlocal cli_cleanup
            spawned, cli_cleanup = popen_cli(
                argv, provider=session_cli_provider, launcher=launcher, **kwargs,
            )
            return spawned

        # Contain the session in its own cgroup scope, exactly as the two
        # sequential spawn paths (run_claude_task, _run_skill_mission) already
        # do. A parallel /implement drives the same build tools, and a Gradle
        # daemon it starts detaches to PPID 1 with its own session — it has
        # left the mission's process group by the time the session ends, so
        # os.killpg can never reach it. launch_scoped sets start_new_session
        # itself, so the group kill on the abort path is unchanged.
        #
        # A missing provider binary still arrives as the FileNotFoundError
        # popen_cli would have raised (launch_scoped pre-checks argv[0] so the
        # systemd-run wrapping cannot swallow it). Parallel dispatch has no
        # exit-127 handler of its own — run.py's caller logs the spawn failure
        # and skips the mission — so it is re-raised unchanged below.
        scoped = mission_scope.launch_scoped(
            cmd,
            spawn=_spawn_in_scope,
            koan_root=os.environ.get("KOAN_ROOT", "") or None,
            stdout=out_f,
            stderr=err_f,
            cwd=wt.path,
            **popen_kwargs,
        )
        proc = scoped.proc
    except Exception:
        # A spawn that got as far as popen_cli holds the provider's invocation
        # lock; releasing it matters more than the stdin fd of a process this
        # path is abandoning anyway — a leaked lock stalls every later mission.
        if cli_cleanup:
            with contextlib.suppress(Exception):
                cli_cleanup()
        if err_f:
            err_f.close()
        if out_f:
            out_f.close()
        if session_tmp:
            cleanup_mission_tmp_dir(session_tmp)
        raise
    session.pid = proc.pid

    # Wrap cleanup to also close file handles and unlink temp prompt files
    # after the process exits.
    def _session_cleanup():
        cli_cleanup()
        out_f.close()
        err_f.close()
        if session_tmp:
            cleanup_mission_tmp_dir(session_tmp)
        if cmd_cleanup_paths:
            try:
                from app.provider import cleanup_managed_paths
                cleanup_managed_paths(cmd_cleanup_paths)
            except Exception as e:
                print(
                    f"[session_manager] sysprompt cleanup error: {e}",
                    file=sys.stderr,
                )

    # Store cleanup, proc and containment as transient state (not persisted).
    # Both exit paths — poll_sessions on completion and kill_session on abort —
    # need the ScopedProcess to tear the scope down.
    session._proc = proc  # type: ignore[attr-defined]
    session._cleanup = _session_cleanup  # type: ignore[attr-defined]
    session._scoped = scoped  # type: ignore[attr-defined]

    # Register in persistent store
    registry.register(session)

    return session


def poll_sessions(
    sessions: List[Session],
    registry: SessionRegistry,
) -> List[SessionResult]:
    """Check subprocess status for active sessions, collect completed ones.

    Args:
        sessions: List of running sessions (must have _proc attribute).
        registry: SessionRegistry for updating state.

    Returns:
        List of SessionResult for newly completed sessions.
    """
    completed = []

    for session in sessions:
        proc = getattr(session, "_proc", None)
        if proc is None:
            continue

        exit_code = proc.poll()
        if exit_code is None:
            continue  # Still running

        # Session completed
        session.status = "done" if exit_code == 0 else "failed"
        session.exit_code = exit_code
        session.finished_at = time.time()

        # Kill everything the session left behind, before the cleanup below
        # reaps its TMPDIR. This is the success path — the one the containment
        # exists for: os.killpg reaches only what stayed in the session's
        # process group, while a build daemon that re-parented to PID 1 with
        # its own session has left it and would survive into the host's idle
        # baseline. A teardown failure must never stop the loop from collecting
        # the other sessions.
        scoped = getattr(session, "_scoped", None)
        memory_cap_detail = ""
        if scoped is not None:
            try:
                scoped.teardown()
            except Exception as e:
                print(
                    f"[session_manager] scope teardown error for session "
                    f"{session.id}: {e}",
                    file=sys.stderr,
                )
            # teardown() is what reads the cgroup's OOM evidence, so the cap
            # verdict only exists after it has run. Reported per session: the
            # sequential loop's global says nothing about this one.
            try:
                memory_cap_detail = str(scoped.cap_message() or "")
            except Exception as e:
                print(
                    f"[session_manager] cap read error for session "
                    f"{session.id}: {e}",
                    file=sys.stderr,
                )

        # Call cleanup
        cleanup = getattr(session, "_cleanup", None)
        if cleanup:
            try:
                cleanup()
            except Exception as e:
                print(f"[session_manager] cleanup error for session {session.id}: {e}", file=sys.stderr)

        # Collect output
        stdout = ""
        stderr = ""
        try:
            if session.stdout_file and Path(session.stdout_file).exists():
                stdout = Path(session.stdout_file).read_text()
        except OSError:
            pass
        try:
            if session.stderr_file and Path(session.stderr_file).exists():
                stderr = Path(session.stderr_file).read_text()
        except OSError:
            pass

        # Update registry
        registry.update(session)

        completed.append(SessionResult(
            session=session,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            memory_cap_detail=memory_cap_detail,
        ))

    return completed


def kill_session(
    session: Session,
    registry: SessionRegistry,
):
    """Terminate a session's subprocess and clean up its worktree.

    Args:
        session: The session to kill.
        registry: SessionRegistry for updating state.
    """
    # Kill subprocess
    proc = getattr(session, "_proc", None)
    if proc is not None and proc.poll() is None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    elif session.pid > 0:
        # No proc reference — try killing by PID
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(session.pid, signal.SIGTERM)

    # Then drop the containment boundary, the way run.py's abort path does:
    # the group kill above reaches only what stayed inside the group, and a
    # daemon that re-parented to PID 1 in its own session left it while alive.
    # In fallback mode teardown IS the same killpg, applied to the group id
    # captured at launch — an addition to the block above (which returns at its
    # poll() guard once the leader is reaped), never a replacement for it.
    scoped = getattr(session, "_scoped", None)
    if scoped is not None:
        try:
            # We SIGKILLed the group ourselves, so a -9 exit is ours and must
            # not be misread as the scope's memory cap firing.
            scoped.teardown(koan_initiated_kill=True)
        except Exception as e:
            print(
                f"[session_manager] scope teardown error for session "
                f"{session.id}: {e}",
                file=sys.stderr,
            )

    # Call cleanup
    cleanup = getattr(session, "_cleanup", None)
    if cleanup:
        try:
            cleanup()
        except Exception as e:
            print(f"[session_manager] cleanup error for session {session.id}: {e}", file=sys.stderr)

    # Update session state
    session.status = "failed"
    session.finished_at = time.time()
    session.exit_code = -1
    registry.update(session)

    # Clean up worktree
    try:
        remove_worktree(
            session.project_path,
            session_id=session.id,
            force=True,
        )
    except Exception as e:
        print(f"[session_manager] worktree removal error for session {session.id}: {e}", file=sys.stderr)

    # Clean up temp files
    for path in (session.stdout_file, session.stderr_file):
        try:
            if path:
                Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def recover_stale_sessions(registry: SessionRegistry):
    """Clean up sessions whose processes are no longer alive.

    Called on startup to handle crash recovery. Also prunes stale
    git worktree references that may have accumulated.
    """
    # Prune stale worktree refs across all projects with active sessions
    pruned_projects: set = set()
    for session in registry.get_all():
        if session.project_path and session.project_path not in pruned_projects:
            pruned_projects.add(session.project_path)
            prune_worktrees(session.project_path)
            # Also reclaim worktrees an agent checked out ad hoc outside the project
            # (typically under /tmp during a PR review) and never removed. These are
            # invisible to prune_worktrees() while their directory still exists, and on a
            # large checkout they are hundreds of megabytes each.
            try:
                reaped = reap_foreign_worktrees(session.project_path)
                if reaped:
                    print(
                        f"[session_manager] reclaimed {len(reaped)} leaked worktree(s) "
                        f"for {session.project_path}",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(
                    f"[session_manager] foreign worktree reap error for "
                    f"{session.project_path}: {e}",
                    file=sys.stderr,
                )

    for session in registry.get_active():
        if session.pid <= 0:
            session.status = "failed"
            session.finished_at = time.time()
            registry.update(session)
            continue

        try:
            os.kill(session.pid, 0)  # Check if process exists
        except ProcessLookupError:
            # Process gone — mark as failed and clean up
            session.status = "failed"
            session.finished_at = time.time()
            registry.update(session)
            try:
                remove_worktree(
                    session.project_path,
                    session_id=session.id,
                    force=True,
                )
            except Exception as e:
                print(f"[session_manager] stale worktree cleanup error for session {session.id}: {e}", file=sys.stderr)
        except PermissionError:
            pass  # Process exists but we can't signal it — leave it
