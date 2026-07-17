"""Chat context building — shared by the bridge and the dedicated chat process.

Extracted from ``awake._build_chat_prompt`` so the dedicated chat process
(``chat_process.py``) can build the *same* prompt without importing the bridge
loop. Personality and memory summary are read through
``bridge_state.get_soul()`` / ``get_summary()`` — mtime-cached fresh reads — so
edits to ``soul.md`` / ``summary.md`` are reflected on the next reply without a
restart (specs/007-chat-process/, FR-004). This is the single builder; both the
inline fallback and the dedicated process call it, so their prompts cannot drift.
"""

import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict

from app.bridge_log import log
from app.bridge_state import (
    CONVERSATION_HISTORY_FILE,
    INSTANCE_DIR,
    KOAN_ROOT,
    MISSIONS_FILE,
    get_soul,
    get_summary,
)
from app.config import get_tools_description
from app.conversation_history import format_conversation_history, load_recent_history
from app.language_preference import get_language_instruction
from app.signals import PAUSE_FILE, STOP_FILE

# ---------------------------------------------------------------------------
# Static context cache — mtime-based invalidation
# ---------------------------------------------------------------------------

_chat_context_cache: Dict[str, tuple] = {}


def load_cached_context(path: Path) -> str:
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


# Cache read_sections() for one poll cycle. build_chat_prompt runs on every
# chat message; without a cache each call opened/closed several SQLite
# connections, feeding arena fragmentation (#2354).
_SECTIONS_CACHE_TTL = 3.0  # seconds — one poll cycle
_sections_cache: Dict[str, object] = {"ts": 0.0, "value": None}


def read_sections_cached(missions_dir):
    """read_sections() with a one-poll-cycle TTL cache (#2354)."""
    from app.mission_store.transition import read_sections
    now = time.time()
    cached = _sections_cache["value"]
    if cached is not None and (now - _sections_cache["ts"]) < _SECTIONS_CACHE_TTL:
        return cached
    sections = read_sections(missions_dir)
    _sections_cache["ts"] = now
    _sections_cache["value"] = sections
    return sections


def build_chat_prompt(text: str, *, lite: bool = False) -> str:
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
    prefs_context = load_cached_context(
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
    else:
        # Store is authoritative; read it (cached one poll cycle) rather than
        # gating on the disposable missions.md export — try/except degrades safely.
        try:
            sections = read_sections_cached(MISSIONS_FILE.parent)
        except Exception as e:
            # read_sections goes through SQLite; a DB read failure
            # (sqlite3.DatabaseError, not an OSError) must degrade to empty
            # chat-context rather than crash chat-prompt building.
            log("warn", f"[chat] chat-context mission read failed: {e}")
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
        emotional_raw = load_cached_context(
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
        return build_chat_prompt(text, lite=True)

    # Last resort: if lite mode still exceeds the cap, truncate user message
    if len(prompt) > MAX_PROMPT_CHARS:
        overflow = len(prompt) - MAX_PROMPT_CHARS
        max_text_len = max(200, len(text) - overflow - 50)  # 50 chars margin for ellipsis/safety
        if len(text) > max_text_len:
            truncated_text = text[:max_text_len] + "… [truncated]"
            prompt = prompt.replace(text, truncated_text)

    return prompt
