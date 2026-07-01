"""Shared orchestration helpers for the native ``/speckit`` skill.

This module centralises the load-bearing safety gates and prompt-assembly
primitives used by both the bridge handlers (``koan/skills/core/speckit*/handler.py``)
and the agent-loop runners (``speckit*_runner.py``). Keeping them in one place
gives each concern exactly one authority (constitution Principle VI) and makes
the safety checks **code-enforced**, not merely prompt-advised (Principle V).

Code-enforced (load-bearing) gates:
  - **Constitution gate** — the target project MUST contain
    ``.specify/memory/constitution.md`` or the run aborts early (FR-003/FR-004).
  - **Quota start-gate** — applied at mission-pickup time in the agent loop
    (``koan/app/mission_executor.py``); this module exposes the configured
    threshold via :func:`app.config.get_speckit_config`.

The pipeline itself (specify -> plan -> tasks -> implement, then best-effort
review/CI and a draft PR) is driven by the skill prompt; the abort-on-step-1-4,
best-effort-step-5-6 contract and the per-task commit cadence are prompt-level
and therefore advisory (Principle V).

Single-writer rule (Principle VI): ``missions.md`` is mutated ONLY through
``app.utils.insert_pending_mission``; progress notes ONLY through
``app.utils.append_to_outbox``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

# Constitution readiness signal (FR-003): its presence authorises speckit.
CONSTITUTION_REL_PATH = Path(".specify") / "memory" / "constitution.md"

# repo:/branch: override tokens, reused from /fix and /implement (FR-007).
_REPO_TOKEN_RE = re.compile(r"\brepo:(\S+)", re.IGNORECASE)
_BRANCH_TOKEN_RE = re.compile(r"\bbranch:(\S+)", re.IGNORECASE)


def constitution_path_for(project_path: str | Path) -> Path:
    """Return the constitution path for a resolved target project."""
    return Path(project_path) / CONSTITUTION_REL_PATH


def has_constitution(project_path: str | Path) -> bool:
    """Code-enforced constitution gate predicate (FR-003/FR-004).

    The constitution at ``<project>/.specify/memory/constitution.md`` is the
    single readiness signal that authorises speckit execution. Its absence MUST
    cause an early abort before any speckit step runs.
    """
    return constitution_path_for(project_path).is_file()


def _pop_token(regex: "re.Pattern[str]", text: str) -> Tuple[Optional[str], str]:
    """Return ``(matched_value, text_with_token_removed)``; ``(None, text)`` if absent."""
    match = regex.search(text)
    if not match:
        return None, text
    return match.group(1), regex.sub("", text)


def extract_overrides(text: str) -> Tuple[Optional[str], Optional[str], str]:
    """Parse and strip ``repo:``/``branch:`` override tokens (FR-007).

    Returns ``(repo, branch, cleaned_text)`` where ``cleaned_text`` has the
    tokens removed and whitespace collapsed. An absent token yields ``None``.
    """
    cleaned = text or ""
    repo, cleaned = _pop_token(_REPO_TOKEN_RE, cleaned)
    branch, cleaned = _pop_token(_BRANCH_TOKEN_RE, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return repo, branch, cleaned


def build_mission_entry(command: str, project_name: str, goal: str) -> str:
    """Build a project-tagged pending mission entry for a speckit command.

    Mirrors ``github_skill_helpers._mission_entry``:
    ``- [project:{name}] /{command} {goal}``. The goal is collapsed to a single
    line so a multiline paste cannot corrupt the one-entry-per-line missions.md
    format that the parser relies on (constitution Principle VI).
    """
    tag = f"[project:{project_name}] " if project_name else ""
    goal = re.sub(r"\s+", " ", (goal or "")).strip()
    body = f"/{command} {goal}".strip() if goal else f"/{command}"
    return f"- {tag}{body}"


def queue_mission(
    instance_dir: str | Path,
    command: str,
    project_name: str,
    goal: str,
    *,
    urgent: bool = False,
) -> bool:
    """Queue a single speckit mission (FR-018 single-mission model).

    Mutates ``missions.md`` ONLY through ``insert_pending_mission``
    (Principle VI — single writer, atomic + locked). Returns ``True`` if
    inserted, ``False`` if it was a duplicate of an already-pending run.
    """
    from app.utils import insert_pending_mission

    missions_path = Path(instance_dir) / "missions.md"
    entry = build_mission_entry(command, project_name, goal)
    return insert_pending_mission(missions_path, entry, urgent=urgent)


def emit_progress(instance_dir: str | Path, message: str) -> None:
    """Append a per-step progress note to the outbox (FR-018).

    The bridge flusher applies the outbound scanner at send time, so only plain
    progress text is written here.
    """
    from app.utils import append_to_outbox

    outbox_path = Path(instance_dir) / "outbox.md"
    append_to_outbox(outbox_path, message.rstrip() + "\n")


def resolve_target(project_arg: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a target project from a name / repo / URL arg (FR-002).

    Returns ``(project_path, project_name)`` or ``(None, None)`` if unresolved.
    Reuses the existing project-resolution path (Principle VII — no parallel
    resolver). Callers surface the "unknown project" case to the operator.
    """
    from app.utils import project_name_for_path, resolve_project_path

    if not project_arg:
        return None, None
    project_path = resolve_project_path(project_arg)
    if not project_path:
        return None, None
    return project_path, project_name_for_path(project_path)
