"""SDLC skill handler.

Handles /sdlc commands from Telegram and the agent bridge.
Creates or resumes a multi-phase SDLC workflow for a GitHub issue.

Usage:
    /sdlc <issue-name> [description]
    /sdlc <issue-name> --resume
    /sdlc <issue-name> --plan
    /sdlc <issue-name> --implement
    /sdlc <issue-name> --review
    /sdlc <issue-name> --approve    (alias for /approve)
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Optional

from app.sdlc_state import (
    SdlcPhase,
    SdlcState,
    load_sdlc_state,
    save_sdlc_state,
)
from app.skills import SkillContext

# Flags that jump to a specific phase (if not already past it)
_PHASE_FLAGS = {
    "--plan": SdlcPhase.PLANNING,
    "--implement": SdlcPhase.IMPLEMENTATION,
    "--review": SdlcPhase.REVIEW,
}

# Phases that can be re-entered via jump flags
_JUMPABLE_FROM = {
    SdlcPhase.PLANNING: {SdlcPhase.RESEARCH, SdlcPhase.ARCHITECTURE},
    SdlcPhase.IMPLEMENTATION: {
        SdlcPhase.RESEARCH, SdlcPhase.ARCHITECTURE,
        SdlcPhase.PLANNING, SdlcPhase.AWAITING_APPROVAL,
    },
    SdlcPhase.REVIEW: {
        SdlcPhase.RESEARCH, SdlcPhase.ARCHITECTURE,
        SdlcPhase.PLANNING, SdlcPhase.AWAITING_APPROVAL,
        SdlcPhase.IMPLEMENTATION,
    },
}


def handle(ctx: SkillContext) -> str:
    args = (ctx.args or "").strip()

    resume = "--resume" in args
    approve = "--approve" in args
    args = re.sub(r"--resume\b", "", args)
    args = re.sub(r"--approve\b", "", args)

    jump_phase: Optional[SdlcPhase] = None
    for flag, phase in _PHASE_FLAGS.items():
        if flag in args:
            jump_phase = phase
            args = re.sub(re.escape(flag), "", args)

    args = args.strip()

    try:
        parts = shlex.split(args)
    except ValueError:
        parts = args.split(None, 1)

    if not parts:
        return (
            "Usage: /sdlc <issue-name> [description]\n"
            "Example: /sdlc add-oauth2 \"Add OAuth2 login\"\n"
            "Flags: --resume | --plan | --implement | --review | --approve"
        )

    issue_name = parts[0]
    description = parts[1] if len(parts) > 1 else ""
    instance_dir = str(ctx.instance_dir)
    project_name = _get_project_name(ctx)

    if approve:
        return _handle_approve(instance_dir, issue_name, project_name, ctx.instance_dir)

    state = load_sdlc_state(instance_dir, issue_name)

    if state is None:
        if resume:
            return f"⚠️ No existing SDLC workflow found for `{issue_name}`. Start without --resume."
        state = SdlcState(
            issue_name=issue_name,
            description=description or f"SDLC workflow for {issue_name}",
            current_phase=SdlcPhase.RESEARCH,
        )
        save_sdlc_state(instance_dir, state)
        _queue_phase_mission(ctx.instance_dir, project_name, issue_name)
        return f"🚀 SDLC started for `{issue_name}` — research phase queued."

    if jump_phase is not None:
        allowed = _JUMPABLE_FROM.get(jump_phase, set())
        if state.current_phase not in allowed:
            return (
                f"⚠️ Cannot jump to {jump_phase.value} — "
                f"current phase is {state.current_phase.value}. "
                "Use --resume to continue from the current phase."
            )
        state.current_phase = jump_phase
        if description:
            state.description = description
        save_sdlc_state(instance_dir, state)
        _queue_phase_mission(ctx.instance_dir, project_name, issue_name)
        return f"⏭️ `{issue_name}` — jumping to {jump_phase.value}."

    if state.current_phase == SdlcPhase.AWAITING_APPROVAL:
        ws = Path(instance_dir) / "sdlc" / issue_name
        plan_note = f"\nPlan at: `{ws / 'PLAN.md'}`" if (ws / "PLAN.md").exists() else ""
        return (
            f"⏸️ `{issue_name}` awaiting approval.{plan_note}\n"
            "Reply /approve <issue-name> to proceed, or /reject to abandon."
        )

    if state.current_phase.is_terminal:
        return (
            f"✅ `{issue_name}` already finished: {state.current_phase.value}. "
            "Start a new workflow with a different issue name."
        )

    if resume or description:
        if description:
            state.description = description
            save_sdlc_state(instance_dir, state)
        _queue_phase_mission(ctx.instance_dir, project_name, issue_name)
        return f"▶️ Resuming `{issue_name}` from {state.current_phase.value}."

    return (
        f"ℹ️ `{issue_name}` in progress (phase: {state.current_phase.value}). "
        "Use --resume to re-queue the current phase."
    )


def _get_project_name(ctx: SkillContext) -> str:
    """Resolve the project name for mission queueing."""
    try:
        from app.project_explorer import get_projects
        projects = get_projects()
        if projects:
            return projects[0][0]
    except Exception:
        pass
    return ""


def _queue_phase_mission(instance_dir: Path, project_name: str, issue_name: str) -> None:
    """Insert /sdlc_phase <issue_name> into missions.md."""
    from app.missions import insert_mission
    from app.utils import atomic_write

    missions_path = instance_dir / "missions.md"
    content = missions_path.read_text(encoding="utf-8") if missions_path.exists() else ""
    project_tag = f"[project:{project_name}] " if project_name else ""
    entry = f"{project_tag}/sdlc_phase {issue_name}"
    updated = insert_mission(content, entry, urgent=False)
    atomic_write(missions_path, updated)


def _handle_approve(
    instance_dir: str, issue_name: str, project_name: str, instance_path: Path
) -> str:
    """Handle --approve flag (shortcut for /approve command)."""
    state = load_sdlc_state(instance_dir, issue_name)
    if state is None:
        return f"⚠️ No SDLC workflow found for `{issue_name}`."
    if state.current_phase != SdlcPhase.AWAITING_APPROVAL:
        return (
            f"⚠️ `{issue_name}` is not awaiting approval "
            f"(phase: {state.current_phase.value})."
        )
    state.approved = True
    state.current_phase = SdlcPhase.IMPLEMENTATION
    save_sdlc_state(instance_dir, state)
    _remove_approval_sentinel(instance_path, issue_name)
    _queue_phase_mission(instance_path, project_name, issue_name)
    return f"✅ `{issue_name}` approved — implementation queued."


def _remove_approval_sentinel(instance_dir: Path, issue_name: str) -> None:
    """Remove the sdlc:awaiting-approval sentinel from missions.md."""
    from app.utils import atomic_write

    missions_path = instance_dir / "missions.md"
    if not missions_path.exists():
        return
    content = missions_path.read_text(encoding="utf-8")
    tag = f"[sdlc:awaiting-approval:{issue_name}]"
    filtered = "\n".join(ln for ln in content.splitlines() if tag not in ln)
    if filtered.rstrip("\n") != content.rstrip("\n"):
        atomic_write(missions_path, filtered + "\n")
