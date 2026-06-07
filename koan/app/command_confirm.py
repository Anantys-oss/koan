"""Chat-suggested command confirmation flow.

The chat model can *offer* to run a slash command in plain language. When it
does, it ends its reply with a structured marker:

    SUGGEST_COMMAND: /recurring run 3

This module is the security boundary for that flow. It never executes anything
itself — it only:

1. Extracts and validates the marker (`extract_suggestion`): the command must
   resolve in the skill registry AND its skill must opt in via
   ``chat_confirmable: true`` in SKILL.md. Anything else is rejected.
2. Holds a single-slot, channel-bound, TTL'd, single-use *pending suggestion*
   (`register_pending` / `take_pending`).
3. Recognises a tight affirmative reply (`is_affirmative`).

The bridge (`awake.handle_message`) reuses the *existing* command path: when a
live pending exists and the human's next message is an affirmative, it replays
the stored literal string through ``handle_message`` exactly as if the human
had typed it. Every existing gate (channel filter, ``handle_command`` dispatch,
skill audience checks, pause/passive state) therefore still applies. No new
trigger authority is introduced — the human's "yes" is the trigger, and the
executed string is the literal one shown at offer time (no LLM in between).
"""

import re
import threading
import time
from typing import Optional

# Default time-to-live for a pending suggestion (seconds). After this, a "yes"
# no longer fires the offered command — the human must ask again.
PENDING_TTL_SECONDS = 300

# Marker the chat model appends to offer a command. Case-insensitive, must be on
# its own line. Captures the literal command (including any subcommand/args).
_MARKER_RE = re.compile(
    r"^[ \t]*SUGGEST_COMMAND:[ \t]*(/[^\n]+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Tight affirmative set — the whole (stripped, lowercased) message must equal one
# of these for a pending command to fire. Anything else clears the pending and
# is handled as normal chat. English + French (soul.md may set either).
_AFFIRMATIVES = frozenset({
    "yes", "y", "ok", "okay", "yep", "yup", "yeah", "sure", "go", "do it",
    "go ahead", "confirm", "confirmed",
    "oui", "ouais", "vas-y", "vas y", "ok go", "c'est bon", "carrément",
})

# Module-level single pending slot. The bridge is single-channel (the inbound
# poll loop filters to ``valid_chat_ids``), so one slot is sufficient; binding it
# to ``chat_id`` is defence in depth against a stray cross-channel "yes".
_LOCK = threading.Lock()
_pending: Optional[dict] = None


def is_affirmative(text: str) -> bool:
    """Return True if ``text`` is a tight, whole-message affirmative."""
    return text.strip().lower() in _AFFIRMATIVES


def extract_suggestion(reply: str, registry) -> tuple:
    """Pull a validated command out of a chat reply.

    Args:
        reply: The chat model's full reply text.
        registry: SkillRegistry used to validate eligibility.

    Returns:
        ``(cleaned_reply, command)`` where ``command`` is the validated literal
        slash command (e.g. ``/recurring run 3``) or ``None`` if the reply had
        no marker or the marker failed validation. ``cleaned_reply`` always has
        every marker line stripped, regardless of validation outcome, so a
        rejected/garbage marker is never shown to the human.
    """
    match = _MARKER_RE.search(reply or "")
    cleaned = _MARKER_RE.sub("", reply or "").strip() if match else (reply or "")
    if not match:
        return cleaned, None

    command = _normalize_command(match.group(1))
    if command and is_confirmable(command, registry):
        return cleaned, command
    return cleaned, None


def _normalize_command(raw: str) -> Optional[str]:
    """Collapse whitespace and reject anything that is not a single-line command."""
    if not raw:
        return None
    # Single line only; collapse internal runs of whitespace to single spaces.
    command = " ".join(raw.split())
    if not command.startswith("/"):
        return None
    # Reject control characters / shell-injection attempts outright. The command
    # is replayed as a Telegram message (not a shell), but keep the surface tight.
    if any(c in command for c in ("\n", "\r", "\t", "`", "$", "|", ";", "&")):
        return None
    return command


def is_confirmable(command: str, registry) -> bool:
    """Return True if ``command``'s skill opts into chat confirmation.

    The first token after ``/`` is the command name; the rest are args/subcommands
    (e.g. ``/recurring run 3`` → command name ``recurring``). Eligibility is
    per-skill: the skill must resolve in the registry and declare
    ``chat_confirmable: true``. Core hardcoded commands (``/stop``, ``/pause``,
    ``/help`` …) are not in the registry, so they are never eligible.
    """
    name = command.lstrip("/").split(None, 1)[0].lower()
    if not name:
        return False
    skill = registry.find_by_command(name)
    return bool(skill is not None and getattr(skill, "chat_confirmable", False))


def register_pending(command: str, chat_id: str, ttl: int = PENDING_TTL_SECONDS,
                     now: Optional[float] = None) -> None:
    """Store ``command`` as the single pending suggestion for ``chat_id``.

    Last-write-wins: a new offer overwrites any prior one (so ``/recurring`` then
    ``/recurring run 3`` works). ``now`` is injectable for tests.
    """
    global _pending
    stamp = time.time() if now is None else now
    with _LOCK:
        _pending = {
            "command": command,
            "chat_id": str(chat_id),
            "expires_at": stamp + ttl,
        }


def clear_pending() -> None:
    """Drop any pending suggestion (e.g. the human moved on)."""
    global _pending
    with _LOCK:
        _pending = None


def peek_pending(chat_id: str, now: Optional[float] = None) -> Optional[str]:
    """Return the live pending command for ``chat_id`` without consuming it.

    Returns ``None`` if there is no pending, it has expired, or it belongs to a
    different channel. Expired entries are cleared as a side effect.
    """
    global _pending
    stamp = time.time() if now is None else now
    with _LOCK:
        if _pending is None:
            return None
        if stamp >= _pending["expires_at"] or _pending["chat_id"] != str(chat_id):
            if stamp >= _pending["expires_at"]:
                _pending = None
            return None
        return _pending["command"]


def take_pending(chat_id: str, now: Optional[float] = None) -> Optional[str]:
    """Consume and return the live pending command for ``chat_id`` (single-use)."""
    global _pending
    command = peek_pending(chat_id, now=now)
    if command is not None:
        with _LOCK:
            _pending = None
    return command
