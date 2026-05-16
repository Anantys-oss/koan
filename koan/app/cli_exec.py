"""CLI execution helpers — secure prompt passing via temp files.

Prevents prompts from leaking into ``ps`` process listings by writing
them to a temporary file (``0o600``) and redirecting that file as the
subprocess stdin.  The ``-p`` argument visible in ``ps`` becomes the
short placeholder ``@stdin`` instead of the full prompt text.

Providers that consume stdin for the prompt (making it unavailable for
the agent's own tool calls) skip this mechanism and pass the prompt
directly as a ``-p`` argument.
"""

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

STDIN_PLACEHOLDER = "@stdin"

# Default timeout for run_cli (seconds).  All current callers pass an
# explicit timeout, but this guards against future callers forgetting.
DEFAULT_TIMEOUT = 600  # 10 minutes


def _uses_stdin_passing() -> bool:
    """Check if the current provider supports stdin-based prompt passing.

    Copilot CLI consumes stdin when reading the ``@stdin`` prompt,
    leaving nothing for its internal agent's tool calls (e.g.
    ``cat /dev/stdin``).  For these providers we pass the prompt
    directly as a ``-p`` argument instead.
    """
    try:
        from app.provider import get_provider_name
        return get_provider_name() not in ("copilot",)
    except Exception as e:
        print(f"[cli_exec] Provider check failed: {e}", file=sys.stderr)
        return True


def prepare_prompt_file(cmd: List[str]) -> Tuple[List[str], Optional[str]]:
    """Extract the ``-p`` prompt from *cmd* and write it to a secure temp file.

    Returns ``(modified_cmd, temp_file_path)``.  If no ``-p`` argument is
    found, it already equals :data:`STDIN_PLACEHOLDER`, or the current
    provider does not support stdin-based prompt passing, returns
    ``(cmd, None)`` unchanged.
    """
    if not _uses_stdin_passing():
        return cmd, None

    try:
        idx = cmd.index("-p")
    except ValueError:
        return cmd, None

    if idx + 1 >= len(cmd):
        return cmd, None

    prompt = cmd[idx + 1]
    if prompt == STDIN_PLACEHOLDER:
        return cmd, None

    fd, path = tempfile.mkstemp(suffix=".md", prefix="koan-prompt-")
    try:
        os.write(fd, prompt.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)

    new_cmd = cmd.copy()
    new_cmd[idx + 1] = STDIN_PLACEHOLDER
    return new_cmd, path


def _cleanup_prompt_file(path: Optional[str]) -> None:
    """Silently remove a temp prompt file if it exists."""
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_cli(cmd, **kwargs) -> subprocess.CompletedProcess:
    """Run a CLI command with the prompt passed via temp-file stdin.

    Drop-in replacement for ``subprocess.run(cmd, stdin=DEVNULL, ...)``.
    A default timeout of :data:`DEFAULT_TIMEOUT` seconds is applied if
    the caller does not provide one, preventing indefinite hangs.
    """
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    cmd, prompt_path = prepare_prompt_file(cmd)
    if prompt_path:
        try:
            with open(prompt_path) as f:
                kwargs.pop("stdin", None)
                kwargs["stdin"] = f
                return subprocess.run(cmd, **kwargs)
        finally:
            _cleanup_prompt_file(prompt_path)
    else:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return subprocess.run(cmd, **kwargs)


def popen_cli(
    cmd, **kwargs
) -> Tuple[subprocess.Popen, Callable[[], None]]:
    """Start a :class:`~subprocess.Popen` process with the prompt via temp-file stdin.

    Returns ``(proc, cleanup)`` where *cleanup()* **must** be called after
    the process exits to close the file handle and delete the temp file.
    """
    cmd, prompt_path = prepare_prompt_file(cmd)
    if prompt_path:
        stdin_file = open(prompt_path)  # noqa: SIM115
        kwargs.pop("stdin", None)
        kwargs["stdin"] = stdin_file
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception:
            stdin_file.close()
            _cleanup_prompt_file(prompt_path)
            raise

        def cleanup():
            stdin_file.close()
            _cleanup_prompt_file(prompt_path)

        return proc, cleanup
    else:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        return subprocess.Popen(cmd, **kwargs), lambda: None


class StreamResult:
    """Result of :func:`stream_with_timeout`."""

    __slots__ = ("stdout", "stderr", "timed_out")

    def __init__(self, stdout: str, stderr: str, timed_out: bool):
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def stream_with_timeout(
    proc: subprocess.Popen,
    timeout: float,
    on_line: Optional[Callable[[str], None]] = None,
    drain_timeout: float = 30.0,
) -> StreamResult:
    """Consume ``proc.stdout`` line-by-line with a process-group-kill watchdog.

    Each stdout line is collected into the returned ``stdout`` text and
    optionally forwarded to *on_line*. After stdout EOF, the stderr
    stream is drained and the subprocess is awaited.

    On timeout the entire process group is SIGKILL'd via
    :func:`os.killpg` — the caller must have started *proc* with
    ``start_new_session=True`` (or ``process_group=0`` on 3.11+) so the
    group exists. Killing the group ensures grandchildren that inherited
    the stdout pipe are torn down too, preventing pipe-drain hangs.

    A ``completed`` flag guarded by a lock closes the race where the
    watchdog Timer fires between the last consumed line and
    ``Timer.cancel()`` — in that window we still want a clean completion
    rather than a spurious timeout.

    Both std streams are closed before returning.
    """
    stdout_lines: List[str] = []
    stderr_text = ""
    timed_out = False
    completed = False
    state_lock = threading.Lock()

    def _kill_process_group() -> None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass

    def _watchdog_fire() -> None:
        # Race guard: if the stream loop has already finished and is
        # about to call watchdog.cancel(), don't flip ``timed_out`` and
        # don't kill — the process is exiting cleanly.
        nonlocal timed_out
        with state_lock:
            if completed:
                return
            timed_out = True
        _kill_process_group()

    watchdog = threading.Timer(timeout, _watchdog_fire)
    watchdog.daemon = True
    watchdog.start()

    try:
        try:
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                stdout_lines.append(stripped)
                if on_line is not None:
                    on_line(stripped)
        finally:
            with state_lock:
                if not timed_out:
                    completed = True
            watchdog.cancel()

        with contextlib.suppress(OSError, ValueError):
            if proc.stderr:
                stderr_text = proc.stderr.read()

        try:
            proc.wait(timeout=drain_timeout)
        except subprocess.TimeoutExpired:
            # Stdout EOF reached but the process refuses to exit —
            # force-kill and report a timeout so callers see the hang.
            timed_out = True
            _kill_process_group()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    return StreamResult(
        stdout="\n".join(stdout_lines).strip(),
        stderr=stderr_text,
        timed_out=timed_out,
    )


# Default backoff durations for CLI retries (seconds).
# Higher than retry.py's (1/2/4s) because CLI calls are heavier.
CLI_RETRY_BACKOFF = (2, 5, 10)
CLI_RETRY_MAX_ATTEMPTS = 3


def run_cli_with_retry(
    cmd,
    *,
    max_attempts: int = CLI_RETRY_MAX_ATTEMPTS,
    backoff: Sequence[float] = CLI_RETRY_BACKOFF,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run a CLI command with automatic retry on transient errors.

    Wraps :func:`run_cli` with error classification: retries on
    ``RETRYABLE`` errors, returns immediately on ``TERMINAL``,
    ``QUOTA``, or ``UNKNOWN`` errors.

    Only suitable for **short-lived** CLI calls (quota probes, format
    commands, reflection invocations).  Do **not** use for long-running
    mission executions managed by the main loop — those use
    :func:`popen_cli` and have their own recovery.

    Args:
        cmd: Command list for subprocess.
        max_attempts: Maximum number of attempts (default 3).
        backoff: Sleep durations between retries.
        **kwargs: Passed through to :func:`run_cli`.

    Returns:
        The :class:`~subprocess.CompletedProcess` from the last attempt.
    """
    from app.cli_errors import ErrorCategory, classify_cli_error

    # Ensure capture_output so we can classify errors
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)

    last_result = None
    for attempt in range(max_attempts):
        result = run_cli(cmd, **kwargs)
        last_result = result

        if result.returncode == 0:
            return result

        category = classify_cli_error(
            result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )

        if category != ErrorCategory.RETRYABLE:
            return result

        if attempt < max_attempts - 1:
            delay = backoff[min(attempt, len(backoff) - 1)]
            err_detail = (result.stderr or "").strip()
            if not err_detail:
                err_detail = (result.stdout or "").strip()[-200:]
            else:
                err_detail = err_detail[:200]
            print(
                f"[cli_exec] Retryable CLI error "
                f"(attempt {attempt + 1}/{max_attempts}): "
                f"{err_detail} "
                f"— retrying in {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)

    return last_result
