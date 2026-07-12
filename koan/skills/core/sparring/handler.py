"""Kōan sparring skill — strategic challenge session."""

import subprocess
from datetime import datetime
from pathlib import Path

from app.bridge_log import log


def handle(ctx):
    """Launch a sparring session via Claude."""
    from app.prompts import load_skill_prompt
    from app.config import get_fast_reply_model

    instance_dir = ctx.instance_dir

    # Notify that we're thinking
    if ctx.send_message:
        ctx.send_message("🧠 Sparring mode activated. I'm thinking...")

    soul = ""
    soul_path = instance_dir / "soul.md"
    if soul_path.exists():
        soul = soul_path.read_text()

    strategy = ""
    strategy_file = instance_dir / "memory" / "global" / "strategy.md"
    if strategy_file.exists():
        strategy = strategy_file.read_text()

    emotional = ""
    emotional_file = instance_dir / "memory" / "global" / "emotional-memory.md"
    if emotional_file.exists():
        emotional = emotional_file.read_text()[:1000]

    prefs = ""
    prefs_file = instance_dir / "memory" / "global" / "human-preferences.md"
    if prefs_file.exists():
        prefs = prefs_file.read_text()

    # Store is authoritative; read it directly rather than gating on the (now
    # generated, possibly-absent) missions.md export.
    from app.mission_store.transition import read_sections
    sections = read_sections(instance_dir)
    in_progress = sections.get("in_progress", [])
    pending = sections.get("pending", [])
    parts = []
    if in_progress:
        parts.append("In progress:\n" + "\n".join(in_progress[:5]))
    if pending:
        parts.append("Pending:\n" + "\n".join(pending[:5]))
    recent_missions = "\n".join(parts)

    hour = datetime.now().hour
    time_hint = (
        "It's late night." if hour >= 22
        else "It's evening." if hour >= 18
        else "It's afternoon." if hour >= 12
        else "It's morning."
    )

    prompt = load_skill_prompt(
        Path(__file__).parent,
        "sparring",
        SOUL=soul,
        PREFS=prefs,
        STRATEGY=strategy,
        EMOTIONAL_MEMORY=emotional,
        RECENT_MISSIONS=recent_missions,
        TIME_HINT=time_hint,
    )

    try:
        from app.cli_provider import build_full_command
        fast_model = get_fast_reply_model()
        cmd = build_full_command(
            prompt=prompt,
            max_turns=1,
            model=fast_model or "",
        )
        from app.cli_exec import run_cli
        result = run_cli(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            response = result.stdout.strip()
            response = response.replace("**", "").replace("```", "")
            # Save sparring response to conversation history
            from app.conversation_history import save_conversation_message
            history_file = instance_dir / "conversation-history.jsonl"
            save_conversation_message(history_file, "assistant", response)
            return response
        else:
            if result.returncode != 0:
                log("error", f"Sparring Claude error (exit {result.returncode}): {result.stderr[:200]}")
            return "🤷 Nothing compelling to say right now. Come back later."
    except subprocess.TimeoutExpired:
        return "⏱ Timeout -- my brain needs more time. Try again."
    except Exception as e:
        log("error", f"Sparring error: {e}")
        return "⚠️ Error during sparring. Try again."
