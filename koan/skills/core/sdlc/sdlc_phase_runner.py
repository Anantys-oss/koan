"""Kōan — SDLC phase runner.

Handles one SDLC phase per invocation. Reads STATE.json to determine
which phase to run, executes the phase-specific prompt via Claude CLI,
writes the output artifact, advances state, and queues the next phase.

CLI:
    python3 -m skills.core.sdlc.sdlc_phase_runner \\
        --issue-name <name> \\
        --project-path <path> \\
        --project-name <name> \\
        --instance-dir <dir>
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.sdlc_state import (
    MAX_FIX_ITERATIONS,
    SdlcPhase,
    archive_sdlc_workspace,
    get_artifact_path,
    get_sdlc_workspace,
    load_sdlc_state,
    save_sdlc_state,
)

# Maximum characters of prior artifacts to inject as context.
_CONTEXT_BUDGET = 80_000

# --- Phase → prompt file mapping ---
_PHASE_PROMPTS: Dict[SdlcPhase, str] = {
    SdlcPhase.RESEARCH: "research",
    SdlcPhase.ARCHITECTURE: "architecture",
    SdlcPhase.PLANNING: "planning",
    SdlcPhase.IMPLEMENTATION: "implementation",
    SdlcPhase.FIX_LOOP: "fix",
    SdlcPhase.DOCUMENTATION: "tech_writer",
}

# Reviewer sub-phases and their prompt/artifact pairs
_REVIEWERS: List[Tuple[str, str]] = [
    ("security_review", "SECURITY.md"),
    ("qa_review", "QA.md"),
    ("sre_review", "SRE.md"),
]

# Phase → output artifact
_PHASE_ARTIFACTS: Dict[SdlcPhase, str] = {
    SdlcPhase.RESEARCH: "RESEARCH.md",
    SdlcPhase.ARCHITECTURE: "ADR.md",
    SdlcPhase.PLANNING: "PLAN.md",
    SdlcPhase.IMPLEMENTATION: "IMPLEMENTATION.md",
    SdlcPhase.DOCUMENTATION: "DOCS.md",
}

# Prior artifacts to inject as context per phase
_PHASE_CONTEXT: Dict[SdlcPhase, List[str]] = {
    SdlcPhase.ARCHITECTURE: ["RESEARCH.md"],
    SdlcPhase.PLANNING: ["RESEARCH.md", "ADR.md"],
    SdlcPhase.IMPLEMENTATION: ["PLAN.md", "RESEARCH.md"],
    SdlcPhase.REVIEW: ["IMPLEMENTATION.md"],
    SdlcPhase.FIX_LOOP: ["PLAN.md", "IMPLEMENTATION.md", "SECURITY.md", "QA.md", "SRE.md"],
    SdlcPhase.DOCUMENTATION: ["IMPLEMENTATION.md", "PLAN.md"],
}

# Linear phase progression for non-branching phases
_NEXT_PHASE: Dict[SdlcPhase, SdlcPhase] = {
    SdlcPhase.RESEARCH: SdlcPhase.ARCHITECTURE,
    SdlcPhase.ARCHITECTURE: SdlcPhase.PLANNING,
    SdlcPhase.PLANNING: SdlcPhase.AWAITING_APPROVAL,
    SdlcPhase.IMPLEMENTATION: SdlcPhase.REVIEW,
    SdlcPhase.FIX_LOOP: SdlcPhase.REVIEW,
    SdlcPhase.DOCUMENTATION: SdlcPhase.PRODUCTION_READY,
}

# Phase emojis for Telegram notifications
_PHASE_EMOJI: Dict[SdlcPhase, str] = {
    SdlcPhase.RESEARCH: "🔍",
    SdlcPhase.ARCHITECTURE: "🏗️",
    SdlcPhase.PLANNING: "📋",
    SdlcPhase.IMPLEMENTATION: "⚙️",
    SdlcPhase.REVIEW: "🔎",
    SdlcPhase.FIX_LOOP: "🔧",
    SdlcPhase.DOCUMENTATION: "📝",
    SdlcPhase.PRODUCTION_READY: "✅",
}


def run_sdlc_phase(
    issue_name: str,
    project_path: str,
    project_name: str,
    instance_dir: str,
) -> int:
    """Run one SDLC phase for *issue_name*.

    Returns:
        0 on success, 1 on unrecoverable error.
    """
    state = load_sdlc_state(instance_dir, issue_name)
    if state is None:
        print(
            f"[sdlc] ERROR: No STATE.json for '{issue_name}'. "
            "Start with /sdlc first.",
            file=sys.stderr,
        )
        return 1

    phase = state.current_phase

    print(
        f"[sdlc] {issue_name} — phase: {phase.value}",
        flush=True,
    )

    if phase == SdlcPhase.AWAITING_APPROVAL:
        print("[sdlc] Workflow paused — awaiting human approval.", flush=True)
        _notify(instance_dir, f"⏸️ [{issue_name}] Awaiting approval — reply /approve {issue_name}")
        return 0

    if phase.is_terminal:
        print(f"[sdlc] Phase is terminal ({phase.value}) — nothing to do.", flush=True)
        return 0

    skill_dir = Path(__file__).resolve().parent
    ws = get_sdlc_workspace(instance_dir, issue_name)
    context = _build_context(ws, phase)

    if phase == SdlcPhase.REVIEW:
        exit_code = _run_review_phase(
            issue_name, project_path, project_name, instance_dir, ws, state, skill_dir, context,
        )
    else:
        exit_code = _run_single_phase(
            phase, issue_name, project_path, project_name,
            instance_dir, ws, state, skill_dir, context,
        )

    return exit_code


def _run_single_phase(
    phase: SdlcPhase,
    issue_name: str,
    project_path: str,
    project_name: str,
    instance_dir: str,
    ws: Path,
    state,
    skill_dir: Path,
    context: str,
) -> int:
    from app.cli_provider import run_command_streaming
    from app.config import get_mission_timeout, get_skill_max_turns
    from app.prompts import load_skill_prompt

    prompt_name = _PHASE_PROMPTS.get(phase)
    if prompt_name is None:
        print(f"[sdlc] ERROR: No prompt for phase {phase.value}", file=sys.stderr)
        return 1

    prompt = load_skill_prompt(
        skill_dir,
        prompt_name,
        ISSUE_NAME=issue_name,
        ISSUE_DESCRIPTION=state.description,
        WORKSPACE_PATH=str(ws),
        PROJECT_ROOT=project_path,
        INSTANCE_DIR=instance_dir,
        PROJECT_NAME=project_name,
    )

    if context:
        prompt = f"{prompt}\n\n---\n## Prior Phase Artifacts\n\n{context}"

    emoji = _PHASE_EMOJI.get(phase, "▶️")
    _notify(instance_dir, f"{emoji} [{issue_name}] Running {phase.value} phase...")

    # Phase timeout: implementation gets 45 min, others 20 min
    phase_timeout = get_mission_timeout() if phase == SdlcPhase.IMPLEMENTATION else 1200
    max_turns = get_skill_max_turns() if phase == SdlcPhase.IMPLEMENTATION else 100

    context_file: Optional[str] = None
    cmd_extra: List[str] = []
    if context:
        fd, context_file = tempfile.mkstemp(prefix="koan-sdlc-", suffix=".txt")
        try:
            import os
            with open(fd, "w", encoding="utf-8") as f:
                f.write(context)
        except OSError:
            os.close(fd)

        cmd_extra = ["--context-file", context_file]

    try:
        output = run_command_streaming(
            prompt=prompt,
            project_path=project_path,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            model_key="mission",
            max_turns=max_turns,
            timeout=phase_timeout,
        )
    except RuntimeError as exc:
        print(f"[sdlc] Phase failed: {exc}", file=sys.stderr)
        _notify(instance_dir, f"❌ [{issue_name}] {phase.value} phase failed.")
        return 1
    finally:
        if context_file:
            import contextlib
            with contextlib.suppress(OSError):
                Path(context_file).unlink(missing_ok=True)

    artifact_name = _PHASE_ARTIFACTS.get(phase)
    if artifact_name:
        artifact_path = ws / artifact_name
        if not artifact_path.exists() or artifact_path.stat().st_size == 0:
            print(
                f"[sdlc] WARNING: Expected artifact {artifact_name} not found after {phase.value}",
                flush=True,
            )

    _advance_phase(issue_name, project_name, instance_dir, ws, state, phase)
    return 0


def _run_review_phase(
    issue_name: str,
    project_path: str,
    project_name: str,
    instance_dir: str,
    ws: Path,
    state,
    skill_dir: Path,
    context: str,
) -> int:
    from app.cli_provider import run_command_streaming
    from app.config import get_skill_max_turns
    from app.prompts import load_skill_prompt

    _notify(instance_dir, f"🔎 [{issue_name}] Starting parallel review (security + QA + SRE)...")

    all_approved = True
    for prompt_name, artifact_name in _REVIEWERS:
        artifact_path = ws / artifact_name
        print(f"[sdlc] Running {prompt_name}...", flush=True)

        reviewer_context = context
        reviewer_context_file: Optional[str] = None

        prompt = load_skill_prompt(
            skill_dir,
            prompt_name,
            ISSUE_NAME=issue_name,
            WORKSPACE_PATH=str(ws),
            PROJECT_ROOT=project_path,
            INSTANCE_DIR=instance_dir,
            PROJECT_NAME=project_name,
        )
        if reviewer_context:
            prompt = f"{prompt}\n\n---\n## Implementation Summary\n\n{reviewer_context}"

        try:
            run_command_streaming(
                prompt=prompt,
                project_path=project_path,
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                model_key="mission",
                max_turns=get_skill_max_turns(),
                timeout=1200,
            )
        except RuntimeError as exc:
            print(f"[sdlc] {prompt_name} failed: {exc}", file=sys.stderr)
            _notify(instance_dir, f"❌ [{issue_name}] {prompt_name} failed.")
            return 1

        if artifact_path.exists():
            content = artifact_path.read_text(encoding="utf-8")
            if "VERDICT: NEEDS_FIX" in content:
                all_approved = False

    failing = _parse_failing_experts(ws)
    state = load_sdlc_state(instance_dir, issue_name)
    if state is None:
        return 1

    if all_approved:
        state.current_phase = SdlcPhase.DOCUMENTATION
        state.failing_experts = []
        save_sdlc_state(instance_dir, state)
        _notify(instance_dir, f"✅ [{issue_name}] All reviews passed — queuing documentation")
    elif state.fix_iteration >= MAX_FIX_ITERATIONS:
        state.current_phase = SdlcPhase.ABANDONED
        save_sdlc_state(instance_dir, state)
        _notify(
            instance_dir,
            f"🚨 [{issue_name}] Fix loop capped at {MAX_FIX_ITERATIONS} — "
            "manual review required."
        )
        archive_sdlc_workspace(instance_dir, issue_name)
        return 1
    else:
        state.current_phase = SdlcPhase.FIX_LOOP
        state.failing_experts = failing
        save_sdlc_state(instance_dir, state)
        _notify(
            instance_dir,
            f"🔧 [{issue_name}] Review done — {len(failing)} expert(s) need fixes. "
            f"Fix loop iteration {state.fix_iteration + 1}/{MAX_FIX_ITERATIONS}"
        )

    _queue_next_phase(issue_name, project_name, instance_dir, state.current_phase)
    return 0


def _advance_phase(
    issue_name: str,
    project_name: str,
    instance_dir: str,
    ws: Path,
    state,
    phase: SdlcPhase,
) -> None:
    next_phase = _NEXT_PHASE.get(phase)
    if next_phase is None:
        return

    state = load_sdlc_state(instance_dir, issue_name)
    if state is None:
        return

    state.current_phase = next_phase
    save_sdlc_state(instance_dir, state)

    if next_phase == SdlcPhase.AWAITING_APPROVAL:
        _queue_approval_sentinel(issue_name, project_name, instance_dir, ws)
        plan_path = ws / "PLAN.md"
        plan_snippet = ""
        if plan_path.exists():
            plan_text = plan_path.read_text(encoding="utf-8")
            plan_snippet = plan_text[:1200]
            if len(plan_text) > 1200:
                plan_snippet += "\n[...truncated...]"
        _notify(
            instance_dir,
            f"⏸️ [{issue_name}] Plan ready for review.\n\n"
            f"{plan_snippet}\n\n"
            f"Reply /approve {issue_name} to proceed with implementation."
        )
        return

    if next_phase == SdlcPhase.PRODUCTION_READY:
        archive_sdlc_workspace(instance_dir, issue_name)
        _notify(instance_dir, f"✅ [{issue_name}] SDLC workflow complete!")
        return

    emoji = _PHASE_EMOJI.get(next_phase, "▶️")
    _notify(instance_dir, f"{emoji} [{issue_name}] Advancing to {next_phase.value}...")
    _queue_next_phase(issue_name, project_name, instance_dir, next_phase)


def _queue_approval_sentinel(
    issue_name: str, project_name: str, instance_dir: str, ws: Path
) -> None:
    """Insert the sdlc:awaiting-approval sentinel into missions.md."""
    from app.missions import insert_mission
    from app.utils import atomic_write

    missions_path = Path(instance_dir) / "missions.md"
    content = missions_path.read_text(encoding="utf-8") if missions_path.exists() else ""
    project_tag = f"[project:{project_name}] " if project_name else ""
    entry = (
        f"{project_tag}"
        f"[sdlc:awaiting-approval:{issue_name}] "
        f"SDLC approval needed for {issue_name}"
    )
    updated = insert_mission(content, entry, urgent=True)
    atomic_write(missions_path, updated)


def _queue_next_phase(
    issue_name: str, project_name: str, instance_dir: str, phase: SdlcPhase
) -> None:
    """Insert the next /sdlc_phase mission into missions.md."""
    if phase.is_terminal or phase == SdlcPhase.AWAITING_APPROVAL:
        return

    from app.missions import insert_mission
    from app.utils import atomic_write

    missions_path = Path(instance_dir) / "missions.md"
    content = missions_path.read_text(encoding="utf-8") if missions_path.exists() else ""
    project_tag = f"[project:{project_name}] " if project_name else ""
    entry = f"{project_tag}/sdlc_phase {issue_name}"
    updated = insert_mission(content, entry, urgent=False)
    atomic_write(missions_path, updated)


def _build_context(ws: Path, phase: SdlcPhase) -> str:
    """Build a context string from prior phase artifacts for injection."""
    artifact_names = _PHASE_CONTEXT.get(phase, [])
    if not artifact_names:
        return ""

    parts = []
    budget = _CONTEXT_BUDGET

    for name in artifact_names:
        path = ws / name
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content.strip():
            continue

        section = f"### {name}\n\n{content}"
        if len(section) > budget:
            truncated = budget - 30
            section = section[:truncated] + "\n[...truncated...]"
            parts.append(section)
            break
        parts.append(section)
        budget -= len(section)
        if budget <= 0:
            break

    return "\n\n".join(parts)


def _parse_failing_experts(ws: Path) -> List[str]:
    """Return list of expert names whose review verdict is NEEDS_FIX."""
    failing = []
    for _, artifact_name in _REVIEWERS:
        path = ws / artifact_name
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "VERDICT: NEEDS_FIX" in content:
            failing.append(artifact_name.replace(".md", "").lower())
    return failing


def _notify(instance_dir: str, message: str) -> None:
    """Append a message to outbox.md for Telegram delivery."""
    outbox = Path(instance_dir) / "outbox.md"
    try:
        with outbox.open("a", encoding="utf-8") as f:
            f.write(f"- {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="Run one SDLC phase")
    p.add_argument("--issue-name", required=True)
    p.add_argument("--project-path", required=True)
    p.add_argument("--project-name", default="")
    p.add_argument("--instance-dir", required=True)
    args = p.parse_args(argv)

    sys.exit(
        run_sdlc_phase(
            issue_name=args.issue_name,
            project_path=args.project_path,
            project_name=args.project_name,
            instance_dir=args.instance_dir,
        )
    )


if __name__ == "__main__":
    main()
