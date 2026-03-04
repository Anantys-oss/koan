"""Google Chat command dispatcher — parse, authorize, execute, respond.

Receives ChatEvents from chat_receiver, parses them into ChatCommands,
checks permissions, dispatches to skill handlers, and returns ChatResponses.
"""

import difflib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config

logger = logging.getLogger("chat_dispatcher")

# ── Skill map (shared with governor_cli.py) ──────────────────────────
# Maps CLI commands to (handler_dir, prepend_command_to_args)
SKILL_MAP = {
    "status":   ("governor.status", False),
    "watcher":  ("governor.watcher", False),
    "advisor":  ("governor.advisor", False),
    "autonomy": ("governor.autonomy", False),
    "rollout":  ("governor.rollout", False),
    "offboard": ("governor.offboard", False),
    "budget":   ("governor/budget", False),
    "keys":     ("governor/keys", False),
    "vault":    ("governor.vault", False),
    "env":      ("governor.env", False),
    "scan":     ("governor.scan", False),
    "report":   ("governor.report", False),
}

ALL_COMMANDS = list(SKILL_MAP.keys()) + ["help"]

# Slash command ID → governor command name
SLASH_COMMAND_MAP = {
    1: "status",
    2: "scan",
    3: "budget",
    4: "report",
    5: "advisor",
    6: "watcher",
    7: "vault",
    8: "help",
}

# Commands that take >5s → send ack before executing
LONG_COMMANDS = {"advisor scan", "advisor analyze", "report daily", "watcher status"}

# Permissions by user type
PERMISSIONS: dict[str, set[str]] = {
    "governor": {"*"},
    "tech": {"status", "budget", "watcher", "advisor", "report", "help"},
    "citizen": {"status", "budget", "help"},
    "unknown": {"help"},
}

# Help text per command
HELP_TEXT = {
    "status": "État général du governor (santé modules, uptime)",
    "watcher": "Surveillance repos GitHub/GitLab (status, log, repos)",
    "advisor": "Détection duplications cross-plateforme (status, scan, report, feedback)",
    "budget": "Budget API LiteLLM (status, list, allocate)",
    "keys": "Gestion clés API virtuelles (list, create, revoke)",
    "vault": "Secrets Google Secret Manager (list, grant, revoke)",
    "env": "Variables d'environnement injectées (list, inject)",
    "scan": "Scan credentials dans le code (scan, report)",
    "report": "Rapports journalier/hebdomadaire (daily, weekly, status)",
    "autonomy": "Niveaux d'autonomie par module (status, set)",
    "rollout": "Groupes de déploiement (status, activate, deactivate)",
    "offboard": "Offboarding utilisateur (check, execute)",
    "help": "Aide et liste des commandes",
}


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class ChatCommand:
    """Parsed governor command from a ChatEvent."""
    skill_name: str
    args: str
    sender_email: str
    sender_type: str
    sender_name: str
    space_name: str
    thread_name: str
    message_name: str
    is_dm: bool
    is_slash_command: bool


@dataclass
class ChatResponse:
    """Response to send back to Google Chat."""
    space_name: str
    thread_name: str
    text: str
    cards: Optional[list] = None
    is_update: bool = False
    update_message_name: str = ""
    is_dm: bool = False


# ── User resolution (cached) ──────────────────────────────────────────

_EMAIL_CACHE: dict[str, tuple[str, str]] = {}
_EMAIL_CACHE_MTIME: float = 0.0


def resolve_sender_type(email: str) -> tuple[str, str]:
    """Resolve a Google Chat sender email to (type, name) via user_registry.yaml.

    Caches the email→(type, name) index and only reloads when the file changes.
    """
    global _EMAIL_CACHE, _EMAIL_CACHE_MTIME

    registry_path = INSTANCE_DIR / "watcher" / "user_registry.yaml"
    try:
        mtime = registry_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    if mtime != _EMAIL_CACHE_MTIME:
        from app.watcher.helpers import load_user_registry
        raw = load_user_registry()
        _EMAIL_CACHE = {
            u["email"]: (u.get("type", "unknown"), u.get("name", u["email"]))
            for u in raw.get("users", [])
            if "email" in u
        }
        _EMAIL_CACHE_MTIME = mtime

    return _EMAIL_CACHE.get(email, ("unknown", email))


# ── Command parsing ──────────────────────────────────────────────────

def parse_command(event) -> ChatCommand:
    """Parse a ChatEvent into a ChatCommand.

    Handles three cases:
    1. Slash command: event.slash_command_id → SLASH_COMMAND_MAP
    2. DM: text parsed directly (no mention prefix)
    3. Space mention: text after @mention extracted
    """
    sender_type, sender_name = resolve_sender_type(event.sender_email)

    # Case 1: Slash command
    if event.slash_command_id is not None:
        skill_name = SLASH_COMMAND_MAP.get(event.slash_command_id, "help")
        args = event.argument_text.strip()
        return ChatCommand(
            skill_name=skill_name,
            args=args,
            sender_email=event.sender_email,
            sender_type=sender_type,
            sender_name=sender_name,
            space_name=event.space_name,
            thread_name=event.thread_name,
            message_name=event.message_name,
            is_dm=event.space_type == "DM",
            is_slash_command=True,
        )

    # Case 2 & 3: Text message (DM or space mention)
    text = event.argument_text.strip() if event.argument_text else event.text.strip()

    parts = text.split(None, 1)
    skill_name = parts[0].lower() if parts else "help"
    args = parts[1] if len(parts) > 1 else ""

    return ChatCommand(
        skill_name=skill_name,
        args=args,
        sender_email=event.sender_email,
        sender_type=sender_type,
        sender_name=sender_name,
        space_name=event.space_name,
        thread_name=event.thread_name,
        message_name=event.message_name,
        is_dm=event.space_type == "DM",
        is_slash_command=False,
    )


# ── Permission check ─────────────────────────────────────────────────

def check_permission(command: ChatCommand) -> Optional[str]:
    """Check if the sender has permission for this command.

    Returns None if authorized, or an error message string if denied.
    """
    allowed = PERMISSIONS.get(command.sender_type, set())
    if "*" in allowed or command.skill_name in allowed:
        return None

    if command.sender_type == "unknown":
        return "Utilisateur non reconnu. Contactez un governor."
    return f"La commande '{command.skill_name}' est réservée aux governors."


# ── Suggest command (fuzzy) ──────────────────────────────────────────

def suggest_command(unknown_input: str) -> tuple[str, list[str]]:
    """Suggest similar commands for an unknown input.

    Returns (error_message, list_of_suggestions).
    """
    matches = difflib.get_close_matches(unknown_input, ALL_COMMANDS, n=3, cutoff=0.4)
    msg = f"Commande inconnue : '{unknown_input}'"
    return msg, matches


# ── Help command ─────────────────────────────────────────────────────

def build_help_text(sender_type: str) -> str:
    """Build help text showing only commands accessible to the sender."""
    allowed = PERMISSIONS.get(sender_type, set())
    lines = ["<b>Commandes AI Governor</b>\n"]

    for cmd, desc in HELP_TEXT.items():
        if "*" in allowed or cmd in allowed:
            lines.append(f"• <b>{cmd}</b> — {desc}")

    if "*" not in allowed:
        hidden = len(HELP_TEXT) - len(lines) + 1
        if hidden > 0:
            lines.append(f"\n<i>{hidden} commandes supplémentaires réservées aux governors.</i>")

    lines.append("\nUsage : <b>@AiGovernor &lt;commande&gt; [args]</b> ou slash command <b>/&lt;commande&gt;</b>")
    return "\n".join(lines)


# ── Skill dispatch ───────────────────────────────────────────────────

def _find_skill(command: str):
    """Find a skill by command name (same logic as governor_cli.py)."""
    from app.skills import Skill, build_registry

    entry = SKILL_MAP.get(command)
    if entry is None:
        return None

    handler_dir, _prepend = entry
    handler_path = INSTANCE_DIR / "skills" / handler_dir / "handler.py"
    if handler_path.exists():
        return Skill(
            name=command,
            scope="governor",
            handler_path=handler_path,
            skill_dir=handler_path.parent,
        )

    extra_dirs = []
    instance_skills = INSTANCE_DIR / "skills"
    if instance_skills.is_dir():
        extra_dirs.append(instance_skills)
    registry = build_registry(extra_dirs)
    qualified_name = handler_dir.replace("/", ".")
    skill = registry.get_by_qualified_name(qualified_name)
    if skill and skill.has_handler():
        return skill

    return None


def _execute_command(command: ChatCommand) -> tuple[str, str]:
    """Execute a governor skill and return (result_text, status).

    Returns:
        (result_text, status) where status is "success" or "error"
    """
    from app.skills import SkillContext, execute_skill

    skill = _find_skill(command.skill_name)
    if skill is None:
        return f"Skill '{command.skill_name}' non trouvé.", "error"

    # Import CLIContext from governor_cli (hybrid dict/attr access for handlers)
    from app.governor_cli import CLIContext

    ctx = CLIContext(
        koan_root=KOAN_ROOT,
        instance_dir=INSTANCE_DIR,
        command_name=command.skill_name,
        args=command.args,
    )

    # Outbox pattern only for skills that use side-effect writes (governor.status)
    _OUTBOX_SKILLS = {"status"}
    outbox_path = INSTANCE_DIR / "outbox.md"
    outbox_before = ""
    if command.skill_name in _OUTBOX_SKILLS:
        try:
            outbox_before = outbox_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass

    result = execute_skill(skill, ctx)

    if result is None and command.skill_name in _OUTBOX_SKILLS:
        try:
            outbox_after = outbox_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            outbox_after = ""
        if outbox_after != outbox_before and outbox_after.startswith(outbox_before):
            result = outbox_after[len(outbox_before):].strip()
            outbox_path.write_text(outbox_before, encoding="utf-8")

    if result is None:
        result = "Commande exécutée (pas de sortie)."

    return result, "success"


def dispatch(command: ChatCommand, send_fn=None) -> ChatResponse:
    """Dispatch a command to the skill handler and return a ChatResponse.

    Args:
        command: Parsed ChatCommand
        send_fn: Optional callable to send intermediate messages (for ack pattern)
    """
    from app.chat_cards import (
        build_command_response_card,
        build_error_card,
        build_ack_card,
        build_permission_denied_card,
    )

    start = time.monotonic()

    # Permission check
    denied = check_permission(command)
    if denied:
        cards = build_permission_denied_card(command.sender_name, command.skill_name, command.sender_type)
        log_chat_audit(command, "denied", 0)
        return ChatResponse(
            space_name=command.space_name,
            thread_name=command.thread_name,
            text=denied,
            cards=cards,
        )

    # Help command (handled inline, no skill dispatch)
    if command.skill_name == "help":
        help_text = build_help_text(command.sender_type)
        cards = build_command_response_card("help", help_text, "info")
        elapsed = int((time.monotonic() - start) * 1000)
        log_chat_audit(command, "success", elapsed)
        return ChatResponse(
            space_name=command.space_name,
            thread_name=command.thread_name,
            text=help_text,
            cards=cards,
        )

    # Unknown command
    if command.skill_name not in SKILL_MAP:
        msg, suggestions = suggest_command(command.skill_name)
        cards = build_error_card(msg, suggestions)
        log_chat_audit(command, "unknown_command", 0)
        return ChatResponse(
            space_name=command.space_name,
            thread_name=command.thread_name,
            text=msg,
            cards=cards,
        )

    # Long command → send ack first
    cmd_key = f"{command.skill_name} {command.args.split()[0]}" if command.args else command.skill_name
    ack_message_name = None
    if cmd_key in LONG_COMMANDS and send_fn:
        ack_cards = build_ack_card(cmd_key)
        ack_resp = ChatResponse(
            space_name=command.space_name,
            thread_name=command.thread_name,
            text=f"Exécution de {cmd_key} en cours...",
            cards=ack_cards,
        )
        ack_message_name = send_fn(ack_resp)

    # Execute skill
    result_text, status = _execute_command(command)
    elapsed = int((time.monotonic() - start) * 1000)
    log_chat_audit(command, status, elapsed)

    cards = build_command_response_card(command.skill_name, result_text, status)

    if ack_message_name:
        return ChatResponse(
            space_name=command.space_name,
            thread_name=command.thread_name,
            text=result_text,
            cards=cards,
            is_update=True,
            update_message_name=ack_message_name,
        )

    return ChatResponse(
        space_name=command.space_name,
        thread_name=command.thread_name,
        text=result_text,
        cards=cards,
    )


# ── Card click handler ───────────────────────────────────────────────

def handle_card_click(event) -> ChatResponse:
    """Handle a CARD_CLICKED event (button press in a Card v2).

    Currently supports: advisor_feedback (relevant, false-positive, ignore).
    """
    from app.chat_cards import build_command_response_card

    action = event.action_name
    params = event.action_params
    sender_type, sender_name = resolve_sender_type(event.sender_email)

    if action == "advisor_feedback":
        detection_id = params.get("detectionId", "")
        feedback = params.get("feedback", "")

        # Execute advisor feedback via skill dispatch
        cmd = ChatCommand(
            skill_name="advisor",
            args=f"feedback {detection_id} {feedback}",
            sender_email=event.sender_email,
            sender_type=sender_type,
            sender_name=sender_name,
            space_name=event.space_name,
            thread_name=event.thread_name,
            message_name=event.message_name,
            is_dm=event.space_type == "DM",
            is_slash_command=False,
        )

        denied = check_permission(cmd)
        if denied:
            from app.chat_cards import build_permission_denied_card
            cards = build_permission_denied_card(sender_name, "advisor feedback", sender_type)
            return ChatResponse(
                space_name=event.space_name,
                thread_name=event.thread_name,
                text=denied,
                cards=cards,
                is_update=True,
                update_message_name=event.message_name,
            )

        result_text, status = _execute_command(cmd)
        cards = build_command_response_card(
            f"advisor feedback {detection_id}",
            f"Verdict : <b>{feedback}</b>\n\n{result_text}",
            status,
        )
        return ChatResponse(
            space_name=event.space_name,
            thread_name=event.thread_name,
            text=result_text,
            cards=cards,
            is_update=True,
            update_message_name=event.message_name,
        )

    # Unknown action
    return ChatResponse(
        space_name=event.space_name,
        thread_name=event.thread_name,
        text=f"Action inconnue : {action}",
    )


# ── Audit journal ────────────────────────────────────────────────────

def log_chat_audit(command: ChatCommand, result_status: str, response_time_ms: int) -> None:
    """Append an audit entry to instance/journal.jsonl."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "gchat",
        "event_type": "command",
        "sender_email": command.sender_email,
        "sender_type": command.sender_type,
        "skill": command.skill_name,
        "args": command.args,
        "space": command.space_name,
        "result_status": result_status,
        "response_time_ms": response_time_ms,
    }

    journal_path = INSTANCE_DIR / "journal.jsonl"
    try:
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("Failed to write audit entry: %s", e)
