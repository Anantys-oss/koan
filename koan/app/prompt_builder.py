"""Kōan — Prompt builder for the agent loop.

Handles agent prompt assembly (template + merge policy + deep research +
verbose mode) and contemplative prompt assembly.

Prompt caching: ``build_agent_prompt_parts()`` splits the assembled prompt
into a stable *system prompt* (merge policy, PR guidelines, verification
gate, etc.) and a variable *user prompt* (agent.md template, mission spec,
drift, deep research). The system prompt is sent via ``--append-system-prompt``
on Claude Code CLI, placing it in the prefix-cached position for better
prompt caching across consecutive missions.

Usage:
    PROMPT=$("$PYTHON" -m app.prompt_builder agent \
        --instance "$INSTANCE" \
        --project-name "$PROJECT_NAME" \
        --project-path "$PROJECT_PATH" \
        --run-num "$RUN_NUM" \
        --max-runs "$MAX_RUNS" \
        --autonomous-mode "${AUTONOMOUS_MODE:-implement}" \
        --focus-area "${FOCUS_AREA:-General autonomous work}" \
        --available-pct "${AVAILABLE_PCT:-50}" \
        --mission-title "$MISSION_TITLE")

    CONTEMPLATE_PROMPT=$("$PYTHON" -m app.prompt_builder contemplative \
        --instance "$INSTANCE" \
        --project-name "$PROJECT_NAME" \
        --session-info "$SESSION_INFO")
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budget-aware context trimming (issue #1309)
# ---------------------------------------------------------------------------

# Pressure levels: control how aggressively prompt sections are trimmed.
PRESSURE_NORMAL = "normal"      # deep mode, >= threshold — full context
PRESSURE_LOW = "low"            # review/implement or moderate budget
PRESSURE_CRITICAL = "critical"  # very low budget — minimal context

# Defaults for each pressure level.
_BUDGET_DEFAULTS = {
    PRESSURE_NORMAL: {
        "memory_entries": 20,
        "learnings_k": 40,
        "learnings_hedge": 5,
        "skip_pr_feedback": False,
        "skip_drift": False,
        "skip_staleness": False,
    },
    PRESSURE_LOW: {
        "memory_entries": 10,
        "learnings_k": 20,
        "learnings_hedge": 3,
        "skip_pr_feedback": True,
        "skip_drift": True,
        "skip_staleness": False,
    },
    PRESSURE_CRITICAL: {
        "memory_entries": 5,
        "learnings_k": 10,
        "learnings_hedge": 2,
        "skip_pr_feedback": True,
        "skip_drift": True,
        "skip_staleness": True,
    },
}

# Threshold defaults: budget % below which pressure escalates.
_DEFAULT_LOW_PCT = 30
_DEFAULT_CRITICAL_PCT = 15


def _read_cfg_int(mapping: dict, key: str, fallback: int) -> int:
    """Read a non-negative int from ``mapping[key]``, defaulting on failure."""
    try:
        value = int(mapping.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    return max(0, value)


def _context_budget(autonomous_mode: str, available_pct: int) -> Dict:
    """Compute context trimming budget from mode and remaining quota.

    Returns a dict with section-level caps and skip flags.  Config
    overrides live under ``context:`` in ``config.yaml``.
    """
    cfg = _load_config_safe()
    ctx = cfg.get("context", {}) or {}

    low_pct = _read_cfg_int(ctx, "low_pressure_pct", _DEFAULT_LOW_PCT)
    critical_pct = _read_cfg_int(ctx, "critical_pressure_pct", _DEFAULT_CRITICAL_PCT)

    # Determine pressure level
    if available_pct < critical_pct:
        pressure = PRESSURE_CRITICAL
    elif autonomous_mode in ("review", "implement") or available_pct < low_pct:
        pressure = PRESSURE_LOW
    else:
        pressure = PRESSURE_NORMAL

    defaults = _BUDGET_DEFAULTS[pressure]

    # Allow per-level config overrides (e.g. context.memory_entries_low: 8)
    suffix = f"_{pressure}" if pressure != PRESSURE_NORMAL else ""
    budget = {
        "pressure": pressure,
        "memory_entries": _read_cfg_int(
            ctx, f"memory_entries{suffix}", defaults["memory_entries"],
        ),
        "learnings_k": _read_cfg_int(
            ctx, f"learnings_k{suffix}", defaults["learnings_k"],
        ),
        "learnings_hedge": _read_cfg_int(
            ctx, f"learnings_hedge{suffix}", defaults["learnings_hedge"],
        ),
        "skip_pr_feedback": defaults["skip_pr_feedback"],
        "skip_drift": defaults["skip_drift"],
        "skip_staleness": defaults["skip_staleness"],
    }

    return budget

# Matches template placeholders like {INSTANCE}, {PROJECT_NAME}, etc.
# Only uppercase letters, digits, and underscores — at least 2 chars to avoid
# false positives on prose like {n} or {x}.
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z_0-9]+)\}")


def _get_caveman_section() -> str:
    """Return the caveman output optimization section if enabled.

    Delegates to :func:`app.caveman.get_caveman_section` so the agent loop
    and skill runners share a single resolution path.  The agent loop has no
    associated skill, so only the global ``optimizations.caveman.enabled``
    flag governs the result here.

    Failures are non-fatal — caveman is an optimization, not a correctness
    feature — but are logged so silent regressions stay visible.  This
    matches the catch-and-log pattern used in
    ``app.prompts._maybe_append_caveman`` and ``app.awake._build_chat_prompt``
    so all three caveman injection sites behave the same way.
    """
    try:
        from app.caveman import get_caveman_section
        return get_caveman_section()
    except Exception as e:
        logger.warning("caveman section unavailable: %s", e)
        return ""


def _get_language_section() -> str:
    """Return the language enforcement section if a preference is set."""
    try:
        from app.language_preference import get_language_instruction
        instruction = get_language_instruction()
        if instruction:
            return f"\n\n# Language Preference\n\n{instruction}\n"
    except (ImportError, OSError):
        pass
    return ""


def _get_rtk_section(project_name: str = "") -> str:
    """Return the RTK awareness section when rtk is enabled for this context.

    Mirrors :func:`_get_caveman_section` but with one extra gate: a project
    can opt out via ``projects.yaml`` even when the global config has rtk
    enabled (``get_project_rtk_enabled``).  The dual gate keeps two
    legitimate concerns separate — "do I want rtk on this Kōan instance"
    and "does this project's tooling tolerate rtk's filters".

    Failures are non-fatal — like caveman, rtk is an optimization, not a
    correctness feature — but are logged so silent regressions stay
    visible.
    """
    try:
        from app.config import is_rtk_awareness_enabled
        if not is_rtk_awareness_enabled():
            return ""
        if project_name:
            from app.projects_config import get_project_rtk_enabled, load_projects_config
            try:
                koan_root = os.environ.get("KOAN_ROOT", "")
                projects_cfg = load_projects_config(koan_root) if koan_root else None
                if projects_cfg and not get_project_rtk_enabled(projects_cfg, project_name):
                    return ""
            except (OSError, ValueError, KeyError):
                # Project resolution failed — fall through to global decision
                # rather than silently dropping the section.
                pass
        from app.prompts import load_prompt
        return "\n\n" + load_prompt("rtk-awareness")
    except (OSError, FileNotFoundError):
        return ""
    except Exception as e:
        logger.warning("rtk awareness section unavailable: %s", e)
        return ""


def _load_config_safe() -> dict:
    """Load config.yaml, returning empty dict on failure."""
    try:
        from app.utils import load_config
        return load_config()
    except (ImportError, OSError, ValueError):
        return {}


def _is_auto_merge_enabled(project_name: str) -> bool:
    """Check if auto-merge is enabled and has rules for the given project."""
    try:
        from app.config import get_auto_merge_config
        config = _load_config_safe()
        merge_cfg = get_auto_merge_config(config, project_name)
        return bool(merge_cfg.get("enabled", True) and merge_cfg.get("rules"))
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return False


def _get_branch_prefix() -> str:
    """Get the configured branch prefix."""
    try:
        from app.config import get_branch_prefix
        return get_branch_prefix()
    except (ImportError, OSError, ValueError):
        return "koan/"


def _get_merge_policy(project_name: str) -> str:
    """Return the merge policy section to append to the agent prompt."""
    from app.prompts import load_prompt

    prefix = _get_branch_prefix()
    if _is_auto_merge_enabled(project_name):
        return load_prompt("merge-policy-enabled", BRANCH_PREFIX=prefix)
    return load_prompt("merge-policy-disabled", BRANCH_PREFIX=prefix)


def _get_deep_research(instance: str, project_name: str, project_path: str) -> str:
    """Get deep research suggestions for DEEP mode."""
    try:
        from app.deep_research import DeepResearch
        research = DeepResearch(Path(instance), project_name, Path(project_path))
        suggestions = research.format_for_agent()
        if suggestions:
            return f"\n\n# Deep Research Analysis\n\n{suggestions}\n"
    except Exception as e:
        print(f"[prompt_builder] Deep research failed: {e}", file=sys.stderr)
    return ""


def _get_focus_section(instance: str) -> str:
    """Build the focus mode section if .koan-focus is active."""
    koan_root = str(Path(instance).parent)
    try:
        from app.focus_manager import check_focus
        state = check_focus(koan_root)
    except Exception as e:
        print(f"[prompt_builder] Focus check failed: {e}", file=sys.stderr)
        return ""

    if state is None:
        return ""

    from app.prompts import load_prompt

    remaining = state.remaining_display()
    return load_prompt("focus-mode", REMAINING=remaining)


def _get_submit_pr_section(project_path: str) -> str:
    """Return the submit-pull-request section (always included)."""
    from app.prompts import load_prompt

    return load_prompt("submit-pull-request", PROJECT_PATH=project_path)


def _get_staleness_section(instance: str, project_name: str) -> str:
    """Get staleness warning for the current project.

    Checks session outcome history and returns a warning if recent sessions
    for this project were non-productive. Cheap operation (local JSON read),
    so it's safe to call in every autonomous mode.
    """
    try:
        from app.session_tracker import get_staleness_warning
        warning = get_staleness_warning(instance, project_name)
        if warning:
            return f"\n\n# Session History Feedback\n\n{warning}\n"
    except Exception as e:
        print(f"[prompt_builder] Staleness check failed: {e}", file=sys.stderr)
    return ""


def _get_pr_feedback_section(project_path: str) -> str:
    """Get PR merge feedback for autonomous topic alignment.

    Summarizes which types of work get merged quickly vs. slowly,
    helping the agent choose high-alignment work. Uses gh CLI
    (network call), so kept lightweight with small limits.
    """
    try:
        from app.pr_feedback import get_alignment_summary
        summary = get_alignment_summary(project_path)
        if summary:
            return (
                f"\n\n# PR Merge Feedback\n\n"
                f"Recent merge patterns for your PRs on this project:\n"
                f"{summary}\n\n"
                f"Use this to guide autonomous topic selection — "
                f"prioritize work types that get merged quickly.\n"
            )
    except Exception as e:
        print(f"[prompt_builder] PR feedback failed: {e}", file=sys.stderr)
    return ""


def _get_drift_section(instance: str, project_name: str, project_path: str) -> str:
    """Get drift summary for the current project.

    Checks how many commits landed on main since the agent's last session
    on this project. Helps the agent avoid conflicts and stale assumptions.
    """
    try:
        from app.session_tracker import get_drift_summary
        summary = get_drift_summary(instance, project_name, project_path)
        if summary:
            return f"\n\n# Codebase Drift\n\n{summary}\n"
    except Exception as e:
        print(f"[prompt_builder] Drift check failed: {e}", file=sys.stderr)
    return ""


def _load_recall_config() -> Tuple[int, int]:
    """Return ``(max_relevant_learnings, recent_hedge)`` from config.yaml.

    Agent-loop defaults are ``(40, 5)`` per issue #1306 — looser than the
    skill-side defaults because the agent loop has more headroom in the
    prompt budget. Reads the shared ``memory:`` block via
    :func:`app.skill_memory.load_recall_config` so both call paths parse
    the same keys with the same coercion rules.
    """
    from app.skill_memory import load_recall_config
    return load_recall_config(default_max=40, default_hedge=5)


def _get_learnings_section(
    instance: str,
    project_name: str,
    mission_title: str,
    focus_area: str,
    max_k_override: int = 0,
    hedge_override: int = 0,
) -> str:
    """Return the project-memory block for the agent prompt.

    Delegates to :func:`app.skill_memory.build_memory_block` so the agent
    loop and mission-driving skills share the same memory-injection logic.
    The block combines three sources:

    * ``memory/projects/{name}/learnings.md`` — Jaccard-filtered against
      the mission text (or ``focus_area`` in autonomous mode), with
      ``max_relevant_learnings`` + ``recall_recent_hedge`` honoured.
    * ``memory/projects/{name}/context.md`` — human-curated, verbatim.
    * ``memory/projects/{name}/priorities.md`` — human-curated, verbatim.

    The ``[recall:full]`` tag in the mission title bypasses learnings
    filtering. Returns an empty string when every source is missing —
    the agent.md template still tells Claude where to read the files
    directly, so this is purely an enrichment hook.

    Args:
        max_k_override: When > 0, overrides config ``max_relevant_learnings``.
            Used by budget-aware context trimming (issue #1309).
        hedge_override: When > 0, overrides config ``recall_recent_hedge``.

    Issue #1306 (learnings recall) + memory-system refactor.
    """
    # Mission text drives scoring. In autonomous mode (no title) fall back
    # to the focus area so the filter still does *something* useful.
    scoring_text = mission_title or focus_area or ""

    from app.skill_memory import build_memory_block

    # Agent loop uses the agent-loop defaults from config.yaml (40, 5) by
    # passing ``None`` overrides; skills override to a tighter budget.
    max_k, hedge = _load_recall_config()

    # Budget-aware override: use tighter caps under low/critical pressure.
    if max_k_override > 0:
        max_k = max_k_override
    if hedge_override > 0:
        hedge = hedge_override

    return build_memory_block(
        instance, project_name, scoring_text,
        max_learnings=max_k,
        recent_hedge=hedge,
        title="Project Memory",
    )


def _get_memory_log_section(
    instance: str, project_name: str, max_entries_override: int = 0,
) -> str:
    """Return recent session/learning history from JSONL truth log.

    Replaces ``scoped_summary()`` as the source of recent project history in
    the agent prompt.  Falls back to ``scoped_summary()`` when the log is
    empty (fresh install before migration runs).

    The window size defaults to 20; configurable via
    ``config.yaml`` ``memory.context_window_entries``.

    Args:
        max_entries_override: When > 0, overrides config value.
            Used by budget-aware context trimming (issue #1309).
    """
    cfg = _load_config_safe()
    mem = cfg.get("memory", {}) or {}
    try:
        max_entries = int(mem.get("context_window_entries", 20))
    except (TypeError, ValueError):
        max_entries = 20

    # Budget-aware override
    if max_entries_override > 0:
        max_entries = max_entries_override

    try:
        from app.memory_manager import read_memory_window, scoped_summary
        entries = read_memory_window(instance, project_name, max_entries=max_entries)
        # Filter out learning entries — _get_learnings_section() already
        # injects task-aware filtered learnings; including them here would
        # duplicate content and waste prompt tokens.
        entries = [e for e in entries if e.get("type") != "learning"]
        if not entries:
            # Fallback: log is empty (fresh install or pre-migration)
            summary = scoped_summary(instance, project_name)
            if summary.strip():
                return f"\n\n# Recent Project History\n\n{summary}\n"
            return ""
        lines = []
        for e in entries:
            ts = e.get("ts", "?")
            etype = e.get("type", "?")
            content = e.get("content", "").strip()
            lines.append(f"[{ts}] {etype}: {content}")
        body = "\n".join(lines)
        return f"\n\n# Recent Project History (last {len(entries)} entries)\n\n{body}\n"
    except Exception as e:
        logger.warning("[prompt_builder] memory log section failed: %s", e)
        return ""


def _get_mission_type_section(mission_title: str) -> str:
    """Return type-specific guidance based on mission classification.

    Classifies the mission title into a work type (debug, implement, etc.)
    and loads the corresponding hint from mission-type-hints.md.
    Returns empty string for "general" type or when no mission is assigned.
    """
    if not mission_title:
        return ""

    try:
        from app.mission_classifier import classify_mission

        mission_type = classify_mission(mission_title)
        if mission_type == "general":
            return ""

        from app.prompts import load_prompt

        hints_text = load_prompt("mission-type-hints")

        # Extract the section for this type (## type\n\ncontent\n\n## next)
        import re

        pattern = rf"^## {re.escape(mission_type)}\n\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, hints_text, re.MULTILINE | re.DOTALL)
        if match:
            hint = match.group(1).strip()
            return (
                f"\n\n# Mission Approach Guidance\n\n"
                f"This looks like a **{mission_type}** mission. "
                f"{hint}\n"
            )
    except Exception as e:
        print(f"[prompt_builder] Mission type hint failed: {e}", file=sys.stderr)
    return ""


def _get_verification_gate_section(mission_title: str) -> str:
    """Return the verification gate section for mission-driven runs.

    Injects verification-before-completion rules that require fresh evidence
    before any success claim. Only included when executing a mission.
    """
    if not mission_title:
        return ""

    from app.prompts import load_prompt

    return load_prompt("verification-gate")


def _get_tdd_section(mission_title: str) -> str:
    """Return the TDD mode section if mission is tagged [tdd]."""
    from app.missions import extract_tdd_tag

    if not mission_title or not extract_tdd_tag(mission_title):
        return ""

    from app.prompts import load_prompt

    return load_prompt("tdd-mode")


def _get_testing_antipatterns_section(mission_title: str) -> str:
    """Return the testing anti-patterns reference for test-involving missions.

    Injected when:
    - Mission is tagged [tdd], OR
    - Mission title contains keywords that typically require test additions

    Skipped for non-testing missions (docs, reviews, analysis) and for
    autonomous mode (no mission title) to avoid wasting context.
    """
    if not mission_title:
        return ""

    from app.missions import extract_tdd_tag

    from app.prompts import load_prompt

    if extract_tdd_tag(mission_title):
        return load_prompt("testing-anti-patterns")

    from app.mission_verifier import expects_tests
    if expects_tests(mission_title):
        return load_prompt("testing-anti-patterns")

    return ""


def _get_verbose_section(instance: str) -> str:
    """Build the verbose mode section if .koan-verbose exists."""
    koan_root = str(Path(instance).parent)
    if not os.path.isfile(os.path.join(koan_root, ".koan-verbose")):
        return ""

    from app.prompts import load_prompt

    return load_prompt("verbose-mode", INSTANCE=instance)


def _get_security_flagging_section(mission_title: str, autonomous_mode: str) -> str:
    """Return the security vulnerability flagging section.

    Only included for mission-driven runs and review/implement autonomous
    modes — not for deep research or wait modes.
    """
    if not mission_title and autonomous_mode not in ("review", "implement"):
        return ""

    from app.prompts import load_prompt

    return load_prompt("security-flagging")


def _build_mission_instruction(mission_title: str, project_name: str) -> str:
    """Build the mission instruction text for the agent prompt."""
    if mission_title:
        from app.prompt_guard import fence_external_data

        fenced = fence_external_data(mission_title, "mission text")
        return (
            f"Your assigned mission is:\n\n{fenced}\n\n"
            "The mission is already marked In Progress. "
            "Follow the Mission Execution Workflow below."
        )
    return (
        f"No specific mission assigned. Look for pending missions for "
        f"{project_name} in missions.md (check [project:{project_name}] "
        f"tags and ### project:{project_name} sub-headers). "
        "If none found, proceed to autonomous mode."
    )


def _warn_unresolved_placeholders(text: str, template_name: str) -> None:
    """Log a warning if any {PLACEHOLDER} tokens remain after substitution."""
    unresolved = _PLACEHOLDER_RE.findall(text)
    if unresolved:
        unique = sorted(set(unresolved))
        logger.warning(
            "[prompt_builder] Unresolved placeholders in '%s': %s",
            template_name,
            ", ".join(f"{{{p}}}" for p in unique),
        )


def _is_focus_mode() -> bool:
    """Return True if focus mode is enabled (config-level or file-based).

    Focus mode disables autonomous GitHub issue pickup — the agent prompt
    replaces the ``GitHub Issue Selection`` section with an explicit
    instruction to only act on explicitly-queued missions.

    Checks both config.yaml/env (permanent) and .koan-focus file (temporary).
    """
    try:
        from app.config import is_focus_mode
        if is_focus_mode():
            return True
    except (ImportError, OSError, ValueError):
        pass
    # Also check file-based focus (.koan-focus from /focus command)
    try:
        koan_root = os.environ.get("KOAN_ROOT", "")
        if koan_root:
            from app.focus_manager import check_focus
            return check_focus(koan_root) is not None
    except (ImportError, OSError, ValueError):
        pass
    return False


_GITHUB_ISSUE_SECTION_RE = re.compile(
    r"## GitHub Issue Selection.*?(?=\n# Autonomy\b|\n## |\Z)",
    re.DOTALL,
)


_FOCUS_MODE_REPLACEMENT = (
    "## Focus Mode (autonomous GitHub pickup disabled)\n\n"
    "Kōan is running in **focus mode**. You MUST NOT pick up "
    "GitHub issues on your own.\n\n"
    "- Only work on the explicit mission assigned above (if any).\n"
    "- If no mission is assigned, do nothing autonomously — exit gracefully.\n"
    "- Do not browse open issues, do not create branches for unassigned work,\n"
    "  do not open speculative PRs.\n"
    "- If the assigned mission references a specific GitHub issue, you may\n"
    "  work on that issue only.\n\n"
)


def _apply_focus_mode_override(prompt: str) -> str:
    """Replace the GitHub Issue Selection section when focus mode is active."""
    if not _is_focus_mode():
        return prompt
    return _GITHUB_ISSUE_SECTION_RE.sub(
        _FOCUS_MODE_REPLACEMENT.rstrip(),
        prompt,
        count=1,
    )


def _load_agent_template(
    instance: str,
    project_name: str,
    project_path: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    focus_area: str,
    available_pct: int,
    mission_title: str,
) -> str:
    """Load and populate the agent.md template with standard placeholders."""
    from app.prompts import load_prompt

    mission_instruction = _build_mission_instruction(mission_title, project_name)
    branch_prefix = _get_branch_prefix()
    result = load_prompt(
        "agent",
        INSTANCE=instance,
        PROJECT_PATH=project_path,
        PROJECT_NAME=project_name,
        RUN_NUM=str(run_num),
        MAX_RUNS=str(max_runs),
        AUTONOMOUS_MODE=autonomous_mode,
        FOCUS_AREA=focus_area,
        AVAILABLE_PCT=str(available_pct),
        MISSION_INSTRUCTION=mission_instruction,
        BRANCH_PREFIX=branch_prefix,
    )
    result = _apply_focus_mode_override(result)
    _warn_unresolved_placeholders(result, "agent")
    return result


def _append_spec(prompt: str, spec_content: str, mission_title: str) -> str:
    """Append mission spec section if applicable."""
    if spec_content and mission_title:
        prompt += (
            "\n\n# Mission Spec\n\n"
            "A spec was generated before implementation. Use it to anchor your work — "
            "follow the approach and stay within the defined scope. Reference key "
            "decisions in the PR description.\n\n"
            f"{spec_content}\n"
        )
    return prompt


def build_agent_prompt(
    instance: str,
    project_name: str,
    project_path: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    focus_area: str,
    available_pct: int,
    mission_title: str = "",
    spec_content: str = "",
) -> str:
    """Build the complete agent prompt from template + dynamic sections.

    Args:
        instance: Path to instance directory
        project_name: Current project name
        project_path: Path to project directory
        run_num: Current run number
        max_runs: Maximum runs per session
        autonomous_mode: Current mode (review/implement/deep)
        focus_area: Description of current focus
        available_pct: Budget percentage available
        mission_title: Mission title (empty for autonomous mode)
        spec_content: Pre-generated mission spec (empty to skip)

    Returns:
        Complete prompt string ready for Claude CLI
    """
    # Compute context budget (issue #1309)
    budget = _context_budget(autonomous_mode, available_pct)
    if budget["pressure"] != PRESSURE_NORMAL:
        logger.info(
            "Context trimming: pressure=%s (mode=%s, pct=%d%%)",
            budget["pressure"], autonomous_mode, available_pct,
        )

    prompt = _load_agent_template(
        instance, project_name, project_path, run_num, max_runs,
        autonomous_mode, focus_area, available_pct, mission_title,
    )

    prompt = _append_spec(prompt, spec_content, mission_title)

    # Append mission type guidance (mission-driven runs only)
    prompt += _get_mission_type_section(mission_title)

    # Append task-aware filtered learnings (issue #1306)
    prompt += _get_learnings_section(
        instance, project_name, mission_title, focus_area,
        max_k_override=budget["learnings_k"],
        hedge_override=budget["learnings_hedge"],
    )

    # Append JSONL memory window (recent sessions + learnings from truth log)
    prompt += _get_memory_log_section(
        instance, project_name,
        max_entries_override=budget["memory_entries"],
    )

    # Append merge policy
    prompt += _get_merge_policy(project_name)

    # Append security vulnerability flagging (mission-driven + review/implement)
    prompt += _get_security_flagging_section(mission_title, autonomous_mode)

    # Append submit-pull-request section
    prompt += _get_submit_pr_section(project_path)

    # Append staleness warning (all autonomous modes — cheap local read)
    if not mission_title and not budget["skip_staleness"]:
        prompt += _get_staleness_section(instance, project_name)

    # Append drift detection (autonomous only — shows what changed on main)
    if not mission_title and not budget["skip_drift"]:
        prompt += _get_drift_section(instance, project_name, project_path)

    # Append PR merge feedback (autonomous only — helps topic alignment)
    if (not mission_title and autonomous_mode in ("deep", "implement")
            and not budget["skip_pr_feedback"]):
        prompt += _get_pr_feedback_section(project_path)

    # Append deep research suggestions (DEEP mode, autonomous only)
    if autonomous_mode == "deep" and not mission_title:
        prompt += _get_deep_research(instance, project_name, project_path)

    # Append TDD mode section if mission is tagged [tdd]
    prompt += _get_tdd_section(mission_title)

    # Append testing anti-patterns reference for [tdd] or test-expecting missions
    prompt += _get_testing_antipatterns_section(mission_title)

    # Append verification gate for mission-driven runs
    prompt += _get_verification_gate_section(mission_title)

    # Append focus mode section if active
    prompt += _get_focus_section(instance)

    # Append verbose mode section if active
    prompt += _get_verbose_section(instance)

    # Append caveman output optimization (token reduction in Claude's output)
    prompt += _get_caveman_section()

    # Append RTK awareness (token reduction in Claude's tool input)
    prompt += _get_rtk_section(project_name)

    # Append language preference (overrides soul.md default)
    prompt += _get_language_section()

    return prompt


def build_agent_prompt_parts(
    instance: str,
    project_name: str,
    project_path: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    focus_area: str,
    available_pct: int,
    mission_title: str = "",
    spec_content: str = "",
) -> Tuple[str, str]:
    """Build agent prompt split into system prompt and user prompt.

    Returns a (system_prompt, user_prompt) tuple. The system prompt
    contains stable content (merge policy, PR guidelines, verification
    gate, etc.) that benefits from prompt caching. The user prompt
    contains the per-mission variable content.

    Callers should pass ``system_prompt`` to ``build_full_command()``
    so it's sent via ``--append-system-prompt`` on supported providers.
    """
    # --- Compute context budget (issue #1309) ---
    budget = _context_budget(autonomous_mode, available_pct)
    if budget["pressure"] != PRESSURE_NORMAL:
        logger.info(
            "Context trimming: pressure=%s (mode=%s, pct=%d%%)",
            budget["pressure"], autonomous_mode, available_pct,
        )

    # --- User prompt: agent template + per-mission dynamic content ---

    user_prompt = _load_agent_template(
        instance, project_name, project_path, run_num, max_runs,
        autonomous_mode, focus_area, available_pct, mission_title,
    )

    user_prompt = _append_spec(user_prompt, spec_content, mission_title)

    # Append mission type guidance (mission-driven runs only)
    user_prompt += _get_mission_type_section(mission_title)

    # Append task-aware filtered learnings (issue #1306).
    # Lives in the user prompt because its content varies with each mission
    # — putting it in the system prompt would defeat prompt caching.
    user_prompt += _get_learnings_section(
        instance, project_name, mission_title, focus_area,
        max_k_override=budget["learnings_k"],
        hedge_override=budget["learnings_hedge"],
    )

    # Append JSONL memory window (recent sessions + learnings from truth log)
    user_prompt += _get_memory_log_section(
        instance, project_name,
        max_entries_override=budget["memory_entries"],
    )

    # Append staleness warning (all autonomous modes — cheap local read)
    if not mission_title and not budget["skip_staleness"]:
        user_prompt += _get_staleness_section(instance, project_name)

    # Append drift detection (autonomous only — shows what changed on main)
    if not mission_title and not budget["skip_drift"]:
        user_prompt += _get_drift_section(instance, project_name, project_path)

    # Append PR merge feedback (autonomous only — helps topic alignment)
    if (not mission_title and autonomous_mode in ("deep", "implement")
            and not budget["skip_pr_feedback"]):
        user_prompt += _get_pr_feedback_section(project_path)

    # Append deep research suggestions (DEEP mode, autonomous only)
    if autonomous_mode == "deep" and not mission_title:
        user_prompt += _get_deep_research(instance, project_name, project_path)

    # --- System prompt: stable sections (best for cache prefix matching) ---
    # These rarely change between consecutive missions on the same project.

    sys_parts = []

    sys_parts.append(_get_merge_policy(project_name))
    sys_parts.append(_get_submit_pr_section(project_path))

    tdd = _get_tdd_section(mission_title)
    if tdd:
        sys_parts.append(tdd)

    antipatterns = _get_testing_antipatterns_section(mission_title)
    if antipatterns:
        sys_parts.append(antipatterns)

    verification = _get_verification_gate_section(mission_title)
    if verification:
        sys_parts.append(verification)

    focus = _get_focus_section(instance)
    if focus:
        sys_parts.append(focus)

    verbose = _get_verbose_section(instance)
    if verbose:
        sys_parts.append(verbose)

    caveman = _get_caveman_section()
    if caveman:
        sys_parts.append(caveman)

    rtk = _get_rtk_section(project_name)
    if rtk:
        sys_parts.append(rtk)

    security = _get_security_flagging_section(mission_title, autonomous_mode)
    if security:
        sys_parts.append(security)

    lang = _get_language_section()
    if lang:
        sys_parts.append(lang)

    system_prompt = "\n\n".join(part for part in sys_parts if part)

    return system_prompt, user_prompt


def build_contemplative_prompt(
    instance: str,
    project_name: str,
    session_info: str,
    github_nickname: str = "",
) -> str:
    """Build the contemplative session prompt from template.

    Args:
        instance: Path to instance directory
        project_name: Current project name
        session_info: Context about current session state
        github_nickname: Bot's GitHub nickname for pre-check instructions.
            Pass empty string (default) when GitHub is not configured — the
            prompt's GitHub section will be omitted automatically.

    Returns:
        Complete contemplative prompt string
    """
    from app.prompts import load_prompt

    prompt = load_prompt(
        "contemplative",
        INSTANCE=instance,
        PROJECT_NAME=project_name,
        SESSION_INFO=session_info,
        GITHUB_NICKNAME=github_nickname,
    )

    # Strip the GitHub pre-check block when no nickname is configured.
    # The block is delimited by {GITHUB_CHECK_BLOCK_START} / {GITHUB_CHECK_BLOCK_END}
    # sentinel lines in the template.
    if not github_nickname:
        import re
        prompt = re.sub(
            r"\{GITHUB_CHECK_BLOCK_START\}.*?\{GITHUB_CHECK_BLOCK_END\}\n?",
            "",
            prompt,
            flags=re.DOTALL,
        )
    else:
        # Remove the sentinel markers, leaving the block content intact.
        prompt = prompt.replace("{GITHUB_CHECK_BLOCK_START}\n", "")
        prompt = prompt.replace("{GITHUB_CHECK_BLOCK_END}\n", "")
        prompt = prompt.replace("{GITHUB_CHECK_BLOCK_START}", "")
        prompt = prompt.replace("{GITHUB_CHECK_BLOCK_END}", "")

    _warn_unresolved_placeholders(prompt, "contemplative")

    # Append language preference (overrides soul.md default)
    prompt += _get_language_section()

    return prompt


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Build prompts for Kōan agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Agent prompt subcommand
    agent_parser = subparsers.add_parser("agent", help="Build agent mission prompt")
    agent_parser.add_argument("--instance", required=True)
    agent_parser.add_argument("--project-name", required=True)
    agent_parser.add_argument("--project-path", required=True)
    agent_parser.add_argument("--run-num", type=int, required=True)
    agent_parser.add_argument("--max-runs", type=int, required=True)
    agent_parser.add_argument("--autonomous-mode", default="implement")
    agent_parser.add_argument("--focus-area", default="General autonomous work")
    agent_parser.add_argument("--available-pct", type=int, default=50)
    agent_parser.add_argument("--mission-title", default="")

    # Contemplative prompt subcommand
    contemplate_parser = subparsers.add_parser(
        "contemplative", help="Build contemplative session prompt"
    )
    contemplate_parser.add_argument("--instance", required=True)
    contemplate_parser.add_argument("--project-name", required=True)
    contemplate_parser.add_argument("--session-info", required=True)
    contemplate_parser.add_argument("--github-nickname", default="")

    args = parser.parse_args()

    if args.command == "agent":
        print(build_agent_prompt(
            instance=args.instance,
            project_name=args.project_name,
            project_path=args.project_path,
            run_num=args.run_num,
            max_runs=args.max_runs,
            autonomous_mode=args.autonomous_mode,
            focus_area=args.focus_area,
            available_pct=args.available_pct,
            mission_title=args.mission_title,
        ))
    elif args.command == "contemplative":
        print(build_contemplative_prompt(
            instance=args.instance,
            project_name=args.project_name,
            session_info=args.session_info,
            github_nickname=args.github_nickname,
        ))


if __name__ == "__main__":
    main()
