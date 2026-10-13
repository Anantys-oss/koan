"""Bridge verbosity level resolution (debug / normal) and gating helper.

Resolution precedence (highest first):
    1. KOAN_MESSAGING_LEVEL env var
    2. instance/.koan-messaging-level state file (written by the skill)
    3. messaging.level in config.yaml
    4. "normal" (default)

Every gated site routes user-facing emissions through ``debug_only`` so that
suppressed messages still land in the log stream for debugging.
"""
import contextlib
import os
import sys
import time
from pathlib import Path

VALID_LEVELS = ("debug", "normal")
DEFAULT_LEVEL = "normal"
STATE_FILE = ".koan-messaging-level"

# Short-TTL memoization for the resolved level. is_debug() is called on every
# mission start/end, every debug_only(), and once per GitHub/Jira mention in a
# batch — without a cache that is one env lookup + stat + read per call. A small
# TTL also prevents the level from flipping mid-batch if the state file races.
_CACHE_TTL = 5.0  # seconds
_cached_level = None
_cached_at = 0.0


def _koan_root() -> Path:
    return Path(os.environ["KOAN_ROOT"])


def _state_path() -> Path:
    return _koan_root() / "instance" / STATE_FILE


def _coerce(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in VALID_LEVELS else DEFAULT_LEVEL


def get_configured_messaging_level() -> str:
    """Persistent default from config.yaml (messaging.level)."""
    try:
        from app.config import get_configured_messaging_level as _cfg
        return _coerce(_cfg())
    except (ImportError, OSError, ValueError, KeyError, AttributeError):
        return DEFAULT_LEVEL


def _resolve_messaging_level() -> str:
    """Resolve: env -> state file -> config.yaml -> 'normal'. Never raises."""
    env = os.environ.get("KOAN_MESSAGING_LEVEL")
    if env:
        return _coerce(env)
    try:
        p = _state_path()
        if p.exists():
            return _coerce(p.read_text())
    except (OSError, KeyError) as e:
        # A KeyError means KOAN_ROOT is unset; an OSError means the state file
        # is unreadable. Leave a trace so an override that failed to apply is
        # diagnosable, then fall back to the config/default level.
        _log("messaging", f"messaging.level state read failed, using config/default: {e}")
    return get_configured_messaging_level()


def get_messaging_level() -> str:
    """Resolved level with short-TTL memoization. Never raises."""
    global _cached_level, _cached_at
    now = time.monotonic()
    if _cached_level is not None and (now - _cached_at) < _CACHE_TTL:
        return _cached_level
    _cached_level = _resolve_messaging_level()
    _cached_at = now
    return _cached_level


def _invalidate_cache() -> None:
    global _cached_level, _cached_at
    _cached_level = None
    _cached_at = 0.0


def is_debug() -> bool:
    return get_messaging_level() == "debug"


def set_messaging_level(level: str) -> str:
    """Write the runtime override state file. Returns the stored level."""
    level = _coerce(level)
    from app.utils import atomic_write
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, level + "\n")
    _invalidate_cache()
    return level


def clear_override() -> None:
    with contextlib.suppress(FileNotFoundError, KeyError):
        _state_path().unlink()
    _invalidate_cache()


def _log(category: str, msg: str) -> None:
    try:
        from app.run_log import log_safe
        log_safe(category, msg)
    except (ImportError, OSError, ValueError):
        # debug_only() promises every suppressed message still reaches the logs.
        # If the normal log sink is unavailable, fall back to stderr so the
        # message is never lost entirely (neither sent nor logged).
        with contextlib.suppress(Exception):
            print(f"[{category}] {msg}", file=sys.stderr)


def debug_only(msg: str, send_fn, *, log_category: str = "bridge") -> None:
    """Always log msg; only invoke send_fn (the user-facing emit) in debug mode.

    Honors the requirement that suppressed messages still reach the logs.
    """
    _log(log_category, msg)
    if is_debug():
        send_fn()


class _ProgressNotifier:
    """notify_fn-compatible callable for *progress* messages.

    Every message is logged unconditionally; it is forwarded to the user
    (``raw_send``) only when messaging.level == "debug". ``raw_send`` exposes the
    un-gated underlying sink (or None for the send_telegram default) so
    notify_outcome() can deliver the outcome line through the same sink without
    inheriting the debug gating.
    """

    def __init__(self, send_fn, log_category: str):
        self.raw_send = send_fn
        self._log_category = log_category

    def __call__(self, msg: str) -> None:
        fn = self.raw_send
        if fn is None:
            from app.notify import send_telegram
            fn = send_telegram
        debug_only(msg, lambda: fn(msg), log_category=self._log_category)


def progress_notify(send_fn=None, *, log_category: str = "bridge"):
    """Return a progress notifier (gated behind messaging.level=debug).

    Drop-in replacement for a raw send_telegram default in skill runners —
    intermediate progress lines become debug-only while the final outcome goes
    through notify_outcome().
    """
    return _ProgressNotifier(send_fn, log_category)


_FAILURE_PREFIXES = ("❌", "🚦")


def failure_reason(line: str) -> str:
    """The reason part of a runner failure line, on one line.

    Runner failure lines read "❌ <What> failed <url>: <reason>"; the reason is
    the text after the first ": " (a URL's "https:" has no space after it). A
    line without one gives its whole text minus the emoji.
    """
    from app.outcome_line import one_line_reason
    _head, sep, tail = line.partition(": ")
    return one_line_reason(tail if sep else line.lstrip("".join(_FAILURE_PREFIXES)))


# Set once this process has reported its outcome. A skill runner is one
# process per mission, so main() can report a failure the run did not.
_outcome_reported = False


def report_failure_unless_reported(skill: str, summary: str, **kwargs) -> None:
    """Report a failed run with *summary* as the reason — unless the run
    already reported its own outcome. The safety net a runner's main() ends with,
    so a failure path that only returned its reason still reaches chat."""
    if not _outcome_reported:
        report_outcome(skill, False, reason=summary, **kwargs)


def hand_over_detail(text: str) -> None:
    """Add *text* under the loop's end message, when the loop owns it.

    For a runner that reports twice (``/rebase`` falling back to ``/recreate``):
    the note says how the last outcome came about. When the runner sends its own
    messages (debug mode), each already says it, so nothing is added.
    """
    from app.outcome_line import DETAIL_MARKER
    if _loop_owns_outcome() and text:
        print("\n".join(f"{DETAIL_MARKER} {line}" for line in _redact(text).splitlines()),
              flush=True)


def _loop_owns_outcome() -> bool:
    return os.environ.get("KOAN_SUPPRESS_RUNNER_OUTCOME") == "1"


def report_outcome(
    skill: str,
    ok: bool,
    *,
    project: str = "",
    project_path: str = "",
    url: str = "",
    detail: str = "",
    reason: str = "",
    send_fn=None,
) -> None:
    """Report a skill runner's final outcome — the one call a runner ends with.

    The message is the fixed template from ``outcome_line``:
    ``✅ [project] 🔍 Reviewed <url>`` (or ``❌ … Review failed: <url>`` + *reason*),
    with *detail* on the lines below. Always logged.

    When the agent loop owns the message (``KOAN_SUPPRESS_RUNNER_OUTCOME=1``:
    normal mode, a loop-dispatched skill) nothing is sent: the outcome is printed
    on stdout as markers, and the loop sends the one message for the mission.
    Otherwise (debug mode, or a runner the loop did not start) the message is
    sent here. ``send_fn`` follows ``notify_outcome``. *project* defaults to the
    name of *project_path*.
    """
    from app import outcome_line as ol

    global _outcome_reported
    _outcome_reported = True
    url, detail, reason = (_redact(text) for text in (url, detail, reason))
    if not project and project_path:
        from app.utils import project_name_for_path
        project = project_name_for_path(project_path)

    if ok:
        first = ol.success_line(project, skill, url)
        text = ol.message(first, detail)
    else:
        first = ol.failure_line(project, skill, url)
        text = ol.message(first, ol.one_line_reason(reason) or ol.NO_REASON_GIVEN, detail)
    _log("outcome", text)
    if _loop_owns_outcome():
        handover = ol.markers(url=url, detail=detail, reason=reason, ok=ok)
        if handover:
            print(handover, flush=True)
        return
    _send_outcome(text, send_fn)


def _redact(text: str) -> str:
    """*text* with secrets masked — a failure reason can quote a token-bearing
    command line or URL, and the outcome reaches chat."""
    if not text:
        return text
    from app.leak_detector import redact_secrets
    return redact_secrets(text)[0]


def _send_outcome(msg: str, send_fn) -> None:
    if isinstance(send_fn, _ProgressNotifier):
        send_fn = send_fn.raw_send
    if send_fn is None:
        from app.notify import send_telegram
        send_fn = send_telegram
    send_fn(msg)


def notify_outcome(msg: str, send_fn=None) -> None:
    """Always log AND send msg.

    Use for the single success/failure outcome line a mission emits (PR url /
    issue url / short failure context). Unlike progress_notify(), this is never
    gated by messaging.level — the outcome is always shown.

    ``send_fn`` may be a progress_notify() notifier; in that case the outcome is
    delivered through its un-gated underlying sink so passing the runner's
    progress notifier here does the right thing. A plain callable is invoked
    directly; None falls back to ``send_telegram``.

    When ``KOAN_SUPPRESS_RUNNER_OUTCOME=1`` is set in the environment (by the
    agent loop for the PR-producing tracked skills /review, /fix, /rebase,
    /implement in normal mode), a runner *success* outcome line is logged only and
    not sent — the agent loop emits the canonical "✅ [project] 🔍 Reviewed <url>"
    completion line instead, so sending here too would duplicate the same URL to
    the user (#2153). The flag is *not* set for /plan: its canonical line cannot
    carry the issue/Jira URL or inline plan body, so the runner's line is the sole
    reporter and must reach chat.

    Only a *single-line* ``✅`` outcome is suppressed (defensive guard): those are
    bare URL restatements the canonical line fully replaces. A multi-line ``✅``
    outcome carries content the canonical line does NOT, so it is always sent.

    A suppressed *failure* line (``❌``/``🚦``) is not sent either: its reason is
    printed to stdout as a ``KOAN_OUTCOME_REASON:`` marker instead, and the agent
    loop sends one message — the fixed "❌ [project] 🔍 Review failed: <url>"
    line with that reason below it.
    """
    _log("outcome", msg)
    stripped = msg.strip()
    if _loop_owns_outcome():
        if stripped.startswith("✅") and "\n" not in stripped:
            return
        if stripped.startswith(_FAILURE_PREFIXES):
            from app.outcome_line import markers
            print(markers(reason=failure_reason(stripped), ok=False), flush=True)
            return
    _send_outcome(msg, send_fn)
