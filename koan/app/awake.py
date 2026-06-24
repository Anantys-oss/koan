#!/usr/bin/env python3
"""
Kōan Messaging Bridge — v2

Fast-response architecture:
- Polls messaging provider every 3s (configurable)
- Chat messages → lightweight Claude call → instant reply
- Mission-like messages → written to missions.md → ack sent immediately
- Outbox flushed every cycle (no more waiting for next poll)
- /stop, /status handled locally (no Claude needed)

Module layout:
- bridge_state.py — shared constants (KOAN_ROOT, INSTANCE_DIR, etc.)
- command_handlers.py — /command dispatch and handler functions
- awake.py (this file) — main loop, chat, outbox, message classification
"""

import contextlib
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple

from app.bridge_log import log
from app.bridge_state import (
    BOT_TOKEN,
    CHAT_ID,
    CHAT_TIMEOUT,
    INSTANCE_DIR,
    KOAN_ROOT,
    MISSIONS_FILE,
    OUTBOX_FILE,
    POLL_INTERVAL,
    PROJECT_PATH,
    SOUL,
    SUMMARY,
    CONVERSATION_HISTORY_FILE,
    TOPICS_FILE,
    _get_registry,
    get_soul,
    get_summary,
)
from app.cli_provider import build_full_command
from app.command_handlers import (
    handle_command,
    handle_mission,
    set_callbacks,
)
from app.health_check import write_heartbeat
from app.language_preference import get_language_instruction
from app.notify import TypingIndicator, reset_flood_state, send_telegram, set_reply_context, clear_reply_context
from app.outbox_manager import OutboxManager, parse_outbox_priority
from app.shutdown_manager import is_shutdown_requested, clear_shutdown
from app.config import (
    get_chat_tools,
    get_tools_description,
    get_model_config,
)
from app.conversation_history import (
    save_conversation_message,
    load_recent_history,
    format_conversation_history,
    compact_history,
)
from app.signals import HEARTBEAT_FILE, PAUSE_FILE, STOP_FILE
from app.utils import (
    atomic_write_json,
    parse_project as _parse_project,
)

_OFFSET_FILE = INSTANCE_DIR / ".telegram-offset.json"


def _load_offset() -> int | None:
    """Load the last persisted Telegram polling offset, or None if absent."""
    try:
        import json
        data = json.loads(_OFFSET_FILE.read_text())
        v = data.get("offset")
        return int(v) if v is not None else None
    except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _save_offset(offset: int) -> None:
    """Persist the Telegram polling offset to disk (best-effort)."""
    with contextlib.suppress(OSError):
        atomic_write_json(_OFFSET_FILE, {"offset": offset})


# ---------------------------------------------------------------------------
# Static chat context cache — mtime-based invalidation
# ---------------------------------------------------------------------------

_chat_context_cache: dict[str, tuple[float, str]] = {}


def _load_cached_context(path: Path) -> str:
    """Load file content with mtime-based caching.

    Avoids re-reading relatively static files (human-preferences.md,
    emotional-memory.md) from disk on every chat request.  Cache is
    invalidated automatically when the file changes.
    """
    if not path.exists():
        return ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cache_key = str(path)
    cached = _chat_context_cache.get(cache_key)
    if cached is not None:
        cached_mtime, cached_content = cached
        if cached_mtime >= mtime:
            return cached_content
    try:
        content = path.read_text().strip()
    except OSError:
        return ""
    _chat_context_cache[cache_key] = (mtime, content)
    return content


# ---------------------------------------------------------------------------
# Outbox manager — singleton instance, created at module load
# ---------------------------------------------------------------------------

_outbox_mgr = OutboxManager(OUTBOX_FILE, INSTANCE_DIR, CONVERSATION_HISTORY_FILE)


def _get_last_message_id() -> int:
    """Get the message_id from the last send_telegram() call."""
    return OutboxManager._get_last_message_id()


def check_config():
    # BOT_TOKEN / CHAT_ID are Telegram-specific.  Slack and Matrix users
    # don't set them — defer the actual credential check to each
    # provider's own ``configure()`` (called from get_messaging_provider
    # below) so non-telegram providers don't get sys.exit(1)'d here.
    from app.messaging import resolve_provider_name
    if resolve_provider_name() == "telegram" and (not BOT_TOKEN or not CHAT_ID):
        log("error", "Set KOAN_TELEGRAM_TOKEN and KOAN_TELEGRAM_CHAT_ID env vars.")
        sys.exit(1)
    if not INSTANCE_DIR.exists():
        log("error", "No instance/ directory. Run: cp -r instance.example instance")
        sys.exit(1)


def get_updates(offset=None):
    """Fetch new updates from the messaging provider.

    Returns a list of raw-dict-compatible updates for backward compatibility
    with the existing message processing pipeline.
    """
    from app.messaging import get_messaging_provider
    provider = get_messaging_provider()
    updates = provider.poll_updates(offset)
    # Convert Update objects to raw dicts for backward compat with main loop
    return [u.raw_data for u in updates]


# ---------------------------------------------------------------------------
# Message classification
# ---------------------------------------------------------------------------

# Patterns that indicate a mission (imperative, actionable request)
MISSION_PATTERNS = [
    r"^(implement|create|add|fix|audit|review|analyze|explore|build|write|run|deploy|test|refactor)\b",
    r"^mission\s*:",
]
MISSION_RE = re.compile("|".join(MISSION_PATTERNS), re.IGNORECASE)


def is_mission(text: str) -> bool:
    """Heuristic: does this message look like a mission assignment?"""
    # Explicit prefix always wins
    if text.lower().startswith("mission:") or text.lower().startswith("mission :"):
        return True
    # Long messages (>200 chars) that start with imperative verbs are likely missions
    if len(text) > 200 and MISSION_RE.match(text):
        return True
    # Short imperative sentences
    if MISSION_RE.match(text):
        return True
    return False


def is_command(text: str) -> bool:
    return text.startswith("/")


def promote_bare_skill_command(text: str) -> Optional[str]:
    """Promote a bare core-skill word to its slash form.

    If the first word of a plain message names a core skill command or alias,
    return the text with a leading slash so ``time`` is handled exactly like
    ``/time``. Only ``core`` skills are promoted — never custom/instance
    skills — so an installed skill colliding with a common word can't hijack
    chat. Returns None when the first word is not a core skill.
    """
    match = re.match(r"[A-Za-z][\w]*", text)
    if not match:
        return None
    skill = _get_registry().find_by_command(match.group(0).lower())
    if skill is not None and skill.scope == "core":
        return "/" + text
    return None


def parse_project(text: str) -> Tuple[Optional[str], str]:
    """Extract [project:name] or [projet:name] from message."""
    return _parse_project(text)


def _is_addressed_to_other_user(text: str, msg: dict, bot_username: str) -> bool:
    """Return True if the message opens with an @mention of someone other than the bot.

    In group chats, ``@other-user do X`` should be ignored — it's addressed
    to a different participant. Only messages starting with ``@our-bot`` (or
    no leading mention at all) should be processed.
    """
    if not bot_username:
        return False

    entities = msg.get("entities", [])
    for entity in entities:
        if entity.get("type") == "mention" and entity.get("offset", -1) == 0:
            length = entity.get("length", 0)
            mentioned = text[:length].lstrip("@")
            return mentioned.lower() != bot_username.lower()

    if text.startswith("@"):
        match = re.match(r"@(\w+)", text)
        if match:
            return match.group(1).lower() != bot_username.lower()

    return False


def _strip_bot_mention_from_text(text: str, msg: dict) -> str:
    """Strip @bot_username mentions from non-command messages.

    In group chats, users often address the bot with ``@BotName hello``.
    This strips the mention so the downstream handlers receive clean text.
    Commands (``/cmd@BotName``) are already handled by ``_strip_bot_mention``
    in command_handlers.py — this covers plain-text mentions.
    """
    if text.startswith("/"):
        return text
    entities = msg.get("entities", [])
    if not entities:
        return text
    # Process entities in reverse offset order so earlier offsets stay valid
    for entity in sorted(entities, key=lambda e: e.get("offset", 0), reverse=True):
        if entity.get("type") == "mention":
            offset = entity.get("offset", 0)
            length = entity.get("length", 0)
            text = text[:offset] + text[offset + length:]
    return text.strip()


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _build_chat_prompt(text: str, *, lite: bool = False) -> str:
    """Build the prompt for a chat response.

    Args:
        text: The user's message.
        lite: If True, strip heavy context (journal, summary) to stay under budget.
    """
    # Load recent conversation history
    history = load_recent_history(CONVERSATION_HISTORY_FILE, max_messages=10)
    history_context = format_conversation_history(history)

    journal_context = ""
    if not lite:
        # Load today's journal for recent context
        from app.journal import read_all_journals
        journal_content = read_all_journals(INSTANCE_DIR, date.today())
        if journal_content:
            if len(journal_content) > 2000:
                journal_context = "...\n" + journal_content[-2000:]
            else:
                journal_context = journal_content

    # Load human preferences for personality context
    prefs_context = _load_cached_context(
        INSTANCE_DIR / "memory" / "global" / "human-preferences.md"
    )

    # Load live progress from pending.md (run in progress)
    pending_context = ""
    pending_path = INSTANCE_DIR / "journal" / "pending.md"
    if pending_path.exists():
        try:
            pending_content = pending_path.read_text()
            # Take last 1500 chars for recent progress
            if len(pending_content) > 1500:
                pending_context = "Live progress (pending.md, last entries):\n...\n" + pending_content[-1500:]
            else:
                pending_context = "Live progress (pending.md):\n" + pending_content
        except OSError:
            pass

    # Load current mission state (live sync with run loop)
    missions_context = ""
    if pending_context:
        missions_context = pending_context
    elif MISSIONS_FILE.exists():
        from app.missions import parse_sections
        try:
            sections = parse_sections(MISSIONS_FILE.read_text())
        except OSError:
            sections = {}
        in_progress = sections.get("in_progress", [])
        pending = sections.get("pending", [])
        if in_progress or pending:
            parts = []
            if in_progress:
                parts.append("In progress: " + "; ".join(in_progress[:3]))
            if pending:
                parts.append(f"Pending: {len(pending)} mission(s)")
            missions_context = "\n".join(parts)

    # Run loop status (CRITICAL for pause awareness)
    run_loop_status = ""
    pause_file = KOAN_ROOT / PAUSE_FILE
    stop_file = KOAN_ROOT / STOP_FILE
    if pause_file.exists():
        run_loop_status = "\n\nRun loop status: ⏸️ PAUSED — Missions are NOT being executed"
    elif stop_file.exists():
        run_loop_status = "\n\nRun loop status: ⛔ STOP REQUESTED — Finishing current work"
    else:
        run_loop_status = "\n\nRun loop status: ▶️ RUNNING"

    # Append run loop status to missions context
    if missions_context:
        missions_context += run_loop_status
    else:
        missions_context = f"No pending missions.{run_loop_status}"

    # Determine time-of-day for natural tone
    hour = datetime.now().hour
    if hour < 7:
        time_hint = "It's very early morning."
    elif hour < 12:
        time_hint = "It's morning."
    elif hour < 18:
        time_hint = "It's afternoon."
    elif hour < 22:
        time_hint = "It's evening."
    else:
        time_hint = "It's late night."

    # Load tools description
    tools_desc = get_tools_description()

    from app.prompts import load_prompt

    summary = get_summary()
    summary_budget = 0 if lite else 1500
    summary_block = f"Summary of past sessions:\n{summary[:summary_budget]}" if summary and summary_budget else ""
    prefs_block = f"About the human:\n{prefs_context}" if prefs_context else ""
    journal_block = f"Today's journal (excerpt):\n{journal_context}" if journal_context else ""
    missions_block = f"Current missions state:\n{missions_context}" if missions_context else ""

    # Load emotional memory for relationship-aware responses
    emotional_context = ""
    if not lite:
        emotional_raw = _load_cached_context(
            INSTANCE_DIR / "memory" / "global" / "emotional-memory.md"
        )
        if emotional_raw:
            # Take last 800 chars — enough for tone, not too heavy
            if len(emotional_raw) > 800:
                emotional_context = "...\n" + emotional_raw[-800:]
            else:
                emotional_context = emotional_raw

    prompt = load_prompt(
        "chat",
        SOUL=get_soul(),
        TOOLS_DESC=tools_desc or "",
        PREFS=prefs_block,
        SUMMARY=summary_block,
        JOURNAL=journal_block,
        MISSIONS=missions_block,
        HISTORY=history_context or "",
        TIME_HINT=time_hint,
        TEXT=text,
    )

    # Inject language preference override
    lang_instruction = get_language_instruction()
    if lang_instruction:
        prompt += f"\n\n{lang_instruction}"

    # Inject caveman directive when enabled and the chat skill hasn't opted out.
    # ``koan/skills/core/chat/SKILL.md`` ships with ``caveman: false`` so this
    # is a no-op by default — but the resolution honours global config + the
    # SKILL.md flag, giving operators a single knob to flip.
    try:
        from app.caveman import append_caveman
        chat_skill_dir = (
            Path(__file__).resolve().parent.parent / "skills" / "core" / "chat"
        )
        prompt = append_caveman(prompt, skill_name="chat", skill_dir=chat_skill_dir)
    except Exception as e:
        log("warn", f"[chat] caveman injection failed: {e}")

    # Inject emotional memory before the user message (if available)
    if emotional_context:
        prompt = prompt.replace(
            f"« {text} »",
            f"Emotional memory (relationship context, use to color your tone):\n{emotional_context}\n\nThe human sends you this message on Telegram:\n\n  « {text} »",
        )

    # Hard cap: if prompt exceeds 12k chars, force lite mode
    MAX_PROMPT_CHARS = 12000
    if len(prompt) > MAX_PROMPT_CHARS and not lite:
        return _build_chat_prompt(text, lite=True)

    # Last resort: if lite mode still exceeds the cap, truncate user message
    if len(prompt) > MAX_PROMPT_CHARS:
        overflow = len(prompt) - MAX_PROMPT_CHARS
        max_text_len = max(200, len(text) - overflow - 50)  # 50 chars margin for ellipsis/safety
        if len(text) > max_text_len:
            truncated_text = text[:max_text_len] + "… [truncated]"
            prompt = prompt.replace(text, truncated_text)

    return prompt


_CHAT_LOCK = threading.Lock()


def _clean_chat_response(text: str, user_message: str = "") -> str:
    """Clean Claude CLI output for Telegram delivery.

    Strips error artifacts, markdown, truncates for smartphone reading,
    and expands bare #123 GitHub refs to clickable URLs.
    """
    from app.text_utils import clean_cli_response, expand_github_refs_auto

    cleaned = clean_cli_response(text)
    return expand_github_refs_auto(cleaned, user_message)


def handle_chat(text: str):
    """Lightweight Claude call for conversational messages — fast response.

    Uses restricted tools (Read/Glob/Grep by default) to prevent prompt
    injection attacks via Telegram messages. No Bash, Edit, or Write access.
    """
    from app.cli_exec import run_cli

    # Save user message to history
    save_conversation_message(CONVERSATION_HISTORY_FILE, "user", text)

    # Scan for prompt injection — warn-only (never block chat; tools are read-only)
    from app.prompt_guard import scan_mission_text
    from app.config import get_prompt_guard_config
    from app.command_handlers import quarantine_mission

    guard_config = get_prompt_guard_config()
    if guard_config["enabled"]:
        guard_result = scan_mission_text(text)
        if guard_result.blocked:
            log("guard", f"WARNING chat: {guard_result.reason} | {text[:100]}")
            quarantine_mission(text, guard_result.reason, source="telegram-chat")

    prompt = _build_chat_prompt(text)
    chat_tools_list = get_chat_tools().split(",")
    models = get_model_config()

    # Run chat from KOAN_ROOT so paths line up with the rest of the system
    # (reflection, agent loop). Chat only needs to read state under
    # ./instance/ (journals, memory, missions) — not Kōan's own source code.
    # The prompt tells Claude where to look.
    chat_cwd = str(KOAN_ROOT)

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=chat_tools_list,
        model=models["chat"],
        fallback=models["fallback"],
        max_turns=5,
    )

    # Serialize chat CLI calls: Claude takes a per-cwd session lock, so two
    # overlapping chats in INSTANCE_DIR collide and one exits 1.
    with _CHAT_LOCK, TypingIndicator():
        try:
            result = run_cli(
                cmd,
                capture_output=True, text=True, timeout=CHAT_TIMEOUT,
                cwd=chat_cwd,
            )
            response = _clean_chat_response(result.stdout.strip(), text)
            if response:
                send_telegram(response)
                msg_id = _get_last_message_id()
                save_conversation_message(
                    CONVERSATION_HISTORY_FILE, "assistant", response,
                    message_id=msg_id, message_type="chat",
                )
                log("chat", f"Chat reply: {response[:80]}...")
            elif result.returncode != 0:
                log("error", f"Claude error (exit {result.returncode}): {result.stderr[:200]}")
                error_msg = "⚠️ Hmm, I couldn't formulate a response. Try again?"
                send_telegram(error_msg)
                save_conversation_message(CONVERSATION_HISTORY_FILE, "assistant", error_msg)
            else:
                log("chat", "Empty response from Claude.")
                empty_msg = "⚠️ I didn't get a response — please try again."
                send_telegram(empty_msg)
                save_conversation_message(CONVERSATION_HISTORY_FILE, "assistant", empty_msg)
        except subprocess.TimeoutExpired:
            log("error", f"Claude timed out ({CHAT_TIMEOUT}s). Retrying with lite context...")
            # Brief backoff before retry to let API pressure ease
            time.sleep(4)
            # Retry with reduced context and shorter timeout
            retry_timeout = CHAT_TIMEOUT // 2
            lite_prompt = _build_chat_prompt(text, lite=True)
            lite_cmd = build_full_command(
                prompt=lite_prompt,
                allowed_tools=chat_tools_list,
                model=models["chat"],
                fallback=models["fallback"],
                max_turns=5,
            )
            try:
                result = run_cli(
                    lite_cmd,
                    capture_output=True, text=True, timeout=retry_timeout,
                    cwd=chat_cwd,
                )
                response = _clean_chat_response(result.stdout.strip(), text)
                if response:
                    send_telegram(response)
                    msg_id = _get_last_message_id()
                    save_conversation_message(
                        CONVERSATION_HISTORY_FILE, "assistant", response,
                        message_id=msg_id, message_type="chat",
                    )
                    log("chat", f"Chat reply (lite retry): {response[:80]}...")
                else:
                    if result.stderr:
                        log("error", f"Lite retry stderr: {result.stderr[:500]}")
                    timeout_msg = f"⏱ Timeout after {CHAT_TIMEOUT}s — try a shorter question, or send 'mission: ...' for complex tasks."
                    send_telegram(timeout_msg)
                    save_conversation_message(CONVERSATION_HISTORY_FILE, "assistant", timeout_msg)
            except subprocess.TimeoutExpired:
                timeout_msg = f"Timeout after {CHAT_TIMEOUT}s — try a shorter question, or send 'mission: ...' for complex tasks."
                send_telegram(timeout_msg)
                save_conversation_message(CONVERSATION_HISTORY_FILE, "assistant", timeout_msg)
            except Exception as e:
                log("error", f"Lite retry error: {e}")
                error_msg = "⚠️ Something went wrong — try again?"
                send_telegram(error_msg)
                save_conversation_message(CONVERSATION_HISTORY_FILE, "assistant", error_msg)
        except Exception as e:
            log("error", f"Claude error: {e}")
            error_msg = "⚠️ Something went wrong — try again?"
            send_telegram(error_msg)
            save_conversation_message(CONVERSATION_HISTORY_FILE, "assistant", error_msg)


# ---------------------------------------------------------------------------
# Outbox — delegated to OutboxManager (backward-compatible wrappers)
#
# These wrappers create a fresh OutboxManager from the current module-level
# values (OUTBOX_FILE, INSTANCE_DIR, etc.) so that test patches on those
# names propagate correctly.  In production, the main loop uses
# _outbox_mgr.flush_async() which goes through the singleton directly.
# ---------------------------------------------------------------------------


def _make_outbox_mgr() -> OutboxManager:
    """Create an OutboxManager from the current (possibly patched) module values."""
    return OutboxManager(OUTBOX_FILE, INSTANCE_DIR, CONVERSATION_HISTORY_FILE)


def _staging_path():
    """Return path of the outbox staging file (crash-recovery backup)."""
    return _make_outbox_mgr().staging_path


# Keep _parse_outbox_priority importable from awake for backward compat
_parse_outbox_priority = parse_outbox_priority


def _recover_staged_outbox():
    """Recover content from a staging file left by a previous crash."""
    _make_outbox_mgr().recover_staged()


def flush_outbox():
    """Relay messages from the run loop outbox."""
    _make_outbox_mgr().flush()


def _requeue_outbox(content: str):
    """Re-append content to outbox.md after a failed send attempt."""
    _make_outbox_mgr().requeue(content)


def _write_outbox_failed(content: str, original_error: Exception):
    """Last-resort persistence: write lost outbox content to outbox-failed.md."""
    _make_outbox_mgr()._write_failed(content, original_error)


def _expand_outbox_github_refs(formatted: str, raw_content: str) -> str:
    """Expand bare #123 GitHub refs in an outbox message to full URLs."""
    return OutboxManager._expand_github_refs(formatted, raw_content)


def _format_outbox_message(raw_content: str) -> str:
    """Format outbox content via Claude with full personality context."""
    return _make_outbox_mgr()._format_message(raw_content)


# ---------------------------------------------------------------------------
# Worker thread — runs handle_chat in background so polling stays responsive
# ---------------------------------------------------------------------------

_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def _run_in_worker(fn, *args):
    """Run fn(*args) in a background thread. One worker at a time.

    Captures the current reply context so that send_telegram() calls
    inside the worker thread reply to the correct message in groups.
    """
    from app.notify import get_reply_context

    global _worker_thread
    reply_to = get_reply_context()

    def _wrapper():
        set_reply_context(reply_to)
        try:
            fn(*args)
        finally:
            clear_reply_context()

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            send_telegram("⏳ Busy with a previous message. Try again in a moment.")
            return
        _worker_thread = threading.Thread(target=_wrapper, daemon=True)
        _worker_thread.start()


# ---------------------------------------------------------------------------
# Outbox flush thread — delegated to OutboxManager
# ---------------------------------------------------------------------------


def _flush_outbox_async():
    """Run flush_outbox() in a background thread if not already running."""
    _outbox_mgr.flush_async()


# Inject callbacks into command_handlers to break circular dependency
set_callbacks(handle_chat=handle_chat, run_in_worker=_run_in_worker)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

REACTIONS_FILE = INSTANCE_DIR / "reactions.jsonl"


def _handle_reaction_update(update: dict):
    """Process a message_reaction update from Telegram.

    Looks up the reacted-to message in conversation history to enrich
    the reaction with context, then stores it in reactions.jsonl.
    """
    from app.reaction_store import save_reaction, lookup_message_context

    reaction_data = update.get("message_reaction", {})
    chat_id = str(reaction_data.get("chat", {}).get("id", ""))
    if chat_id != CHAT_ID:
        return

    message_id = reaction_data.get("message_id", 0)
    if not message_id:
        return

    new_emojis = {
        e.get("emoji", "")
        for e in reaction_data.get("new_reaction", [])
        if e.get("type") == "emoji"
    }
    old_emojis = {
        e.get("emoji", "")
        for e in reaction_data.get("old_reaction", [])
        if e.get("type") == "emoji"
    }

    added = new_emojis - old_emojis
    removed = old_emojis - new_emojis

    # Look up original message context
    context = lookup_message_context(CONVERSATION_HISTORY_FILE, message_id)
    text_preview = ""
    msg_type = ""
    if context:
        text_preview = context.get("text", "")[:100]
        msg_type = context.get("message_type", "")

    for emoji in added:
        save_reaction(
            REACTIONS_FILE, message_id, emoji,
            is_added=True,
            original_text_preview=text_preview,
            message_type=msg_type,
        )
        log("reaction", f"Reaction {emoji} added on message {message_id}")

    for emoji in removed:
        save_reaction(
            REACTIONS_FILE, message_id, emoji,
            is_added=False,
            original_text_preview=text_preview,
            message_type=msg_type,
        )
        log("reaction", f"Reaction {emoji} removed from message {message_id}")


def handle_message(text: str):
    text = text.strip()
    if not text:
        return

    # Each incoming user message resets flood protection so identical
    # command responses (e.g. /help twice) are never suppressed.
    reset_flood_state()

    if is_command(text):
        handle_command(text)
        return

    promoted = promote_bare_skill_command(text)
    if promoted is not None:
        handle_command(promoted)
        return

    if is_mission(text):
        handle_mission(text)
    else:
        _run_in_worker(handle_chat, text)


def _check_group_chat_mode(provider) -> None:
    """Detect group chats and verify the bot can actually read every message.

    In groups, bots with Telegram Privacy Mode enabled (the default) only
    receive /commands, @mentions, and replies — not regular messages. A bot can
    read *every* message only if privacy mode is disabled
    (``can_read_all_group_messages``) **or** the bot is a group administrator.

    This probes both via the Bot API. When the bot is blocked, it warns loudly
    (log + a message into the group itself) so the cause of an apparently
    "ignored" chat is obvious instead of silent.
    """
    import requests

    if provider.get_provider_name() != "telegram":
        return
    try:
        api_base = provider.get_api_base()
        chat_id = provider.get_channel_id()
        resp = requests.get(f"{api_base}/getChat", params={"chat_id": chat_id}, timeout=5)
        data = resp.json()
        if not data.get("ok"):
            log("warn", f"getChat failed: {data.get('description', 'unknown')}")
            return
        chat_type = data.get("result", {}).get("type", "")
        if chat_type not in ("group", "supergroup"):
            return

        log("init", f"Chat type: {chat_type} — group mode active")

        # The bot receives every message only if privacy mode is disabled OR it
        # is a group admin. Probe getMe (privacy flag + bot id), then — only if
        # still needed — getChatMember (admin status).
        can_read_all = False
        bot_id = None
        try:
            me = requests.get(f"{api_base}/getMe", timeout=5).json()
            if me.get("ok"):
                result = me.get("result", {})
                bot_id = result.get("id")
                can_read_all = bool(result.get("can_read_all_group_messages"))
        except Exception as e:
            log("warn", f"getMe failed: {e}")

        is_admin = False
        if not can_read_all and bot_id is not None:
            try:
                member = requests.get(
                    f"{api_base}/getChatMember",
                    params={"chat_id": chat_id, "user_id": bot_id},
                    timeout=5,
                ).json()
                if member.get("ok"):
                    status = member.get("result", {}).get("status", "")
                    is_admin = status in ("administrator", "creator")
            except Exception as e:
                log("warn", f"getChatMember failed: {e}")

        if can_read_all or is_admin:
            log("init", "Group mode: bot can read all messages ✓")
            return

        # Blocked: privacy mode on and not an admin → plain messages never arrive.
        log("warn", "Privacy Mode is ON — bot only sees /commands, @mentions, and replies in this group")
        log("warn", "Fix: @BotFather /setprivacy → Disable then re-add the bot, OR promote the bot to admin")
        try:
            provider.send_message(
                "⚠️ I can't see regular messages in this group because Telegram "
                "Privacy Mode is enabled.\n\n"
                "To let me reply to every message (like a 1:1 chat):\n"
                "1. Message @BotFather → /setprivacy → select me → Disable, then "
                "remove and re-add me to this group.\n"
                "   — or —\n"
                "2. Promote me to administrator in this group.\n\n"
                "Until then I only respond to /commands, @mentions, and replies."
            )
        except Exception as e:
            log("warn", f"Failed to send privacy-mode warning: {e}")
    except Exception as e:
        log("warn", f"Group chat detection failed: {e}")


def _ensure_runner_alive() -> None:
    """Start the runner if it's not running.

    Called after a /restart re-exec so the bridge can bring the runner
    back when the runner wasn't alive to detect the restart signal itself.
    """
    from app.pid_manager import check_pidfile, start_runner

    if check_pidfile(KOAN_ROOT, "run"):
        return  # Already running — it will restart itself via exit code 42

    log("init", "Runner not running — starting it as part of restart")
    ok, msg = start_runner(KOAN_ROOT)
    if ok:
        log("init", f"Runner started: {msg}")
    else:
        log("error", f"Failed to start runner: {msg}")


MAX_BRIDGE_CRASHES = 5
BRIDGE_BACKOFF_MULTIPLIER = 10
MAX_BRIDGE_BACKOFF = 60


def _bridge_loop():
    from app.banners import print_bridge_banner
    from app.github_auth import setup_github_auth
    from app.pid_manager import acquire_pidfile, release_pidfile
    from app.restart_manager import check_restart, clear_restart, reexec_bridge

    check_config()

    # Ensure PYTHONPATH includes the koan/ package directory so that
    # subprocess calls (e.g. python -m app.issue_cli) can resolve app.*
    # modules regardless of the subprocess CWD.
    koan_pkg_dir = str(KOAN_ROOT / "koan")
    current = os.environ.get("PYTHONPATH", "")
    if koan_pkg_dir not in current.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            f"{koan_pkg_dir}{os.pathsep}{current}" if current else koan_pkg_dir
        )

    # Run pending data migrations (e.g. French→English header conversion)
    from app.migration_runner import run_pending_migrations
    applied = run_pending_migrations()
    if applied:
        log("init", f"Applied {len(applied)} migration(s)")

    # Enforce single instance — abort if another awake process is running
    pidfile_lock = acquire_pidfile(KOAN_ROOT, "awake")

    setup_github_auth()

    from app.messaging import resolve_provider_name
    provider_name = resolve_provider_name()
    print_bridge_banner(f"messaging bridge — {provider_name.lower()}")

    # Record startup time — used to ignore stale signal files in the
    # main loop (only react to files created after we started).
    startup_time = time.time()

    # Compact old conversation history to avoid context bleed across sessions
    compacted = compact_history(CONVERSATION_HISTORY_FILE, TOPICS_FILE)
    if compacted:
        log("health", f"Compacted {compacted} old messages at startup")

    # Purge stale heartbeat so health_check doesn't report STALE on restart
    heartbeat_file = KOAN_ROOT / HEARTBEAT_FILE
    heartbeat_file.unlink(missing_ok=True)
    write_heartbeat(str(KOAN_ROOT))
    if BOT_TOKEN:
        log("init", f"Token: ...{BOT_TOKEN[-8:]}")
    if CHAT_ID:
        log("init", f"Chat ID: {CHAT_ID}")
    log("init", f"Soul: {len(SOUL)} chars loaded")
    log("init", f"Summary: {len(SUMMARY)} chars loaded")
    registry = _get_registry()
    core_count = len(registry.list_by_scope("core"))
    extra_count = len(registry) - core_count
    skills_info = f"{core_count} core"
    if extra_count:
        skills_info += f" + {extra_count} extra"
    log("init", f"Skills: {skills_info}")

    # Initialize messaging provider and log startup banner
    from app.messaging import get_messaging_provider
    try:
        provider = get_messaging_provider()
        provider_name = provider.get_provider_name().upper()
        channel_id = provider.get_channel_id()
        log("init", f"Messaging provider: {provider_name}, Channel: {channel_id}")
    except SystemExit:
        log("error", "Failed to initialize messaging provider")
        sys.exit(1)

    bot_username = provider.get_bot_username()
    if bot_username:
        log("init", f"Bot username: @{bot_username}")

    # Detect group chat and warn about privacy mode
    _check_group_chat_mode(provider)

    # Optional GitHub webhook receiver — push-based notification triggering.
    # Defaults off; only starts when github.webhook.enabled and a secret are set.
    try:
        from app.github_webhook import maybe_start_from_config
        if maybe_start_from_config(str(KOAN_ROOT)) is not None:
            log("init", "GitHub webhook receiver started (push-based triggering)")
    except Exception as e:
        # Keep the bridge alive on webhook failure, but log the full traceback —
        # a bare {e} loses the context needed to diagnose startup failures.
        import traceback
        log("error",
            f"GitHub webhook receiver failed to start: {e}\n{traceback.format_exc()}")

    log("init", f"Polling every {POLL_INTERVAL}s (chat mode: fast reply)")
    offset = _load_offset()
    if offset is not None:
        log("init", f"Resuming Telegram polling from persisted offset {offset}")
    first_poll = True

    try:
        while True:
            try:
                updates = get_updates(offset)
            except StopIteration:
                raise
            except Exception as e:
                log("error", f"get_updates failed: {e}")
                time.sleep(POLL_INTERVAL)
                continue

            for update in updates:
                # Telegram uses update_id for offset-based pagination.
                # Other providers (matrix, slack, discord) manage their own
                # cursor internally and may hand us updates that don't carry
                # this key. Never let a missing/malformed update_id crash the
                # bridge: a single non-conforming update would otherwise take
                # down main(), the supervisor would restart us, the same
                # poison message would be re-delivered, and we'd crash-loop
                # forever (see logs/awake.log KeyError: 'update_id').
                if "update_id" in update:
                    offset = update["update_id"] + 1
                    _save_offset(offset)

                # Handle reaction updates
                if "message_reaction" in update:
                    try:
                        _handle_reaction_update(update)
                    except Exception as e:
                        log("error", f"Reaction handling failed: {e}")
                    continue

                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                # Match against either: (a) the active provider's channel
                # id (resolved at startup — covers slack/matrix where
                # CHAT_ID is unset), or (b) CHAT_ID (telegram-only, kept
                # for backward compat with existing tests that patch it
                # directly).  For telegram in production the two are the
                # same value.
                #
                # message_id / mention-stripping MUST be derived inside this
                # block, not a separate `chat_id == CHAT_ID` guard: for matrix
                # (and any provider where CHAT_ID is unset) chat_id matches
                # channel_id but never CHAT_ID, so a CHAT_ID-only guard leaves
                # message_id unbound and set_reply_context() below raises
                # UnboundLocalError — crashing the bridge on every message.
                #
                # Empty strings are stripped from the match set and an empty
                # chat_id is rejected: with CHAT_ID="" (normal for matrix/slack)
                # a malformed update missing chat.id would otherwise satisfy
                # `"" in (channel_id, "")` and slip past the channel filter.
                valid_chat_ids = {str(channel_id), str(CHAT_ID)} - {""}
                if text and chat_id and chat_id in valid_chat_ids:
                    if _is_addressed_to_other_user(text, msg, bot_username):
                        log("chat", f"Ignoring message addressed to another user: {text[:60]}")
                        continue
                    message_id = msg.get("message_id", 0)
                    text = _strip_bot_mention_from_text(text, msg)
                    log("chat", f"Received: {text[:60]}")
                    set_reply_context(message_id)
                    try:
                        handle_message(text)
                    except Exception as e:
                        log("error", f"Message handling failed: {e}")
                        try:
                            send_telegram(f"⚠️ Error processing message: {type(e).__name__}: {e}")
                        except Exception as notify_err:
                            print(f"[bridge] error notification also failed: {notify_err}", file=sys.stderr)
                    finally:
                        clear_reply_context()

            # After the first poll cycle, clear any stale signal files
            # left from a previous incarnation.  During the first poll
            # these files act as dedup guards: if Telegram re-delivers
            # the /restart or /shutdown message that triggered our exit,
            # the skill handler re-creates the file — but we clear it
            # right after so the check below finds nothing.
            if first_poll:
                # Check if we're coming back from a /restart before clearing
                was_restart = check_restart(str(KOAN_ROOT), target="bridge")
                clear_restart(str(KOAN_ROOT), target="bridge")
                clear_shutdown(str(KOAN_ROOT))
                first_poll = False

                # If this is a restart-triggered re-exec and the runner
                # is dead, start it.  The runner can't self-restart if
                # it wasn't running when the signal was created.
                if was_restart:
                    _ensure_runner_alive()

            try:
                _flush_outbox_async()
            except Exception as e:
                log("error", f"flush_outbox failed: {e}")

            try:
                write_heartbeat(str(KOAN_ROOT))
            except Exception as e:
                log("error", f"write_heartbeat failed: {e}")

            # Check for restart signal (set by /restart command).
            # Only react to files created AFTER we started — stale files
            # were already cleared above after the first poll.
            if check_restart(str(KOAN_ROOT), since=startup_time, target="bridge"):
                log("init", "Restart signal detected. Re-executing...")
                release_pidfile(pidfile_lock, KOAN_ROOT, "awake")
                reexec_bridge()

            # Check for /shutdown signal (timestamp-validated)
            if is_shutdown_requested(str(KOAN_ROOT), startup_time):
                log("init", "Shutdown requested. Exiting.")
                clear_shutdown(str(KOAN_ROOT))
                release_pidfile(pidfile_lock, KOAN_ROOT, "awake")
                sys.exit(0)

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        release_pidfile(pidfile_lock, KOAN_ROOT, "awake")
        log("init", "Shutting down.")
        sys.exit(0)


def main():
    """Entry point with crash recovery wrapper.

    Handles: normal exit, CTRL-C, and unexpected crashes with backoff.
    Mirrors the pattern in run.py to keep the bridge alive through transient
    failures (network blips, provider errors, file I/O hiccups).
    """
    import traceback

    crash_count = 0
    while True:
        try:
            _bridge_loop()
            break
        except KeyboardInterrupt:
            break
        except SystemExit:
            raise
        except Exception:
            crash_count += 1
            tb = traceback.format_exc()
            print(
                f"[bridge] Unexpected crash ({crash_count}/{MAX_BRIDGE_CRASHES}): {tb}",
                file=sys.stderr,
            )

            if crash_count >= MAX_BRIDGE_CRASHES:
                print(
                    f"[bridge] Too many crashes ({MAX_BRIDGE_CRASHES}). Giving up.",
                    file=sys.stderr,
                )
                sys.exit(1)

            backoff = min(
                BRIDGE_BACKOFF_MULTIPLIER * crash_count, MAX_BRIDGE_BACKOFF
            )
            print(f"[bridge] Restarting in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)


if __name__ == "__main__":
    main()
