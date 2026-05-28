"""Reaction storage for Telegram message reactions.

Stores reactions as JSONL (one reaction per line) for lightweight,
append-only persistence. Supports loading recent reactions and
correlating them with conversation history messages.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def save_reaction(
    reactions_file: Path,
    message_id: int,
    emoji: str,
    is_added: bool,
    original_text_preview: str = "",
    message_type: str = "",
):
    """Append a reaction to the reactions JSONL file.

    Args:
        reactions_file: Path to reactions.jsonl
        message_id: Telegram message_id of the reacted-to message
        emoji: The emoji string (e.g., "\U0001f44d", "\U0001f44e")
        is_added: True if reaction was added, False if removed
        original_text_preview: First ~100 chars of the original message
        message_type: Origin context of the message (chat, conclusion, notification)
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "message_id": message_id,
        "emoji": emoji,
        "action": "added" if is_added else "removed",
    }
    if original_text_preview:
        entry["original_text_preview"] = original_text_preview[:100]
    if message_type:
        entry["message_type"] = message_type

    try:
        from app.locked_file import locked_jsonl_append
        locked_jsonl_append(reactions_file, entry)
    except OSError as e:
        print(f"[reaction_store] Error saving reaction: {e}")


def load_recent_reactions(
    reactions_file: Path,
    max_reactions: int = 50,
) -> List[Dict]:
    """Load the most recent reactions from the JSONL file.

    Args:
        reactions_file: Path to reactions.jsonl
        max_reactions: Maximum number of reactions to return

    Returns:
        List of reaction dicts, most recent last
    """
    if not reactions_file.exists():
        return []

    try:
        from app.locked_file import locked_jsonl_read
        lines = locked_jsonl_read(reactions_file)
    except OSError:
        return []

    reactions = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                reactions.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return reactions[-max_reactions:] if len(reactions) > max_reactions else reactions


def lookup_message_context(
    history_file: Path,
    message_id: int,
) -> Optional[Dict]:
    """Find a message in conversation history by its message_id.

    Args:
        history_file: Path to conversation-history.jsonl
        message_id: Telegram message_id to look up

    Returns:
        Message dict if found, None otherwise
    """
    if not history_file.exists():
        return None

    try:
        from app.locked_file import locked_jsonl_read
        lines = locked_jsonl_read(history_file)
    except OSError:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("message_id") == message_id:
                return msg
        except json.JSONDecodeError:
            continue

    return None


def compact_reactions(reactions_file: Path, keep: int = 200):
    """Compact the reactions file to keep only the most recent entries.

    Args:
        reactions_file: Path to reactions.jsonl
        keep: Number of recent reactions to retain
    """
    if not reactions_file.exists():
        return

    reactions = load_recent_reactions(reactions_file, max_reactions=keep)
    if not reactions:
        return

    from app.utils import atomic_write
    body = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in reactions)
    atomic_write(reactions_file, body)
