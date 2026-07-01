"""Kōan -- speckit runner.

Runs the native spec-kit (``/speckit``) pipeline in the target project:
``specify -> plan -> tasks -> implement`` (committing once per task), then a
best-effort private review loop, CI/test validation, and a draft PR.

The runner is **auto-discovered** by ``skill_dispatch._discover_runner_module``
(convention: ``skills/core/speckit/speckit_runner.py``) and follows the standard
runner CLI contract (``--project-path``, ``--project-name``, ``--instance-dir``,
``--context-file``), so no ``skill_dispatch.py`` registration is required
(constitution Principle VII — extend the existing mechanism, don't fork it).

Safety gates:
  - Constitution gate (FR-003): enforced at the bridge handler AND re-checked
    here as defense-in-depth.
  - Quota start-gate (FR-017): enforced at mission pickup in mission_executor.

The pipeline itself is driven by the speckit prompt; the abort-on-step-1-4,
best-effort-step-5-6 contract and the per-task commit cadence are prompt-level
and therefore advisory (constitution Principle V — only the code-enforced gates
above are load-bearing).

CLI:
    python3 -m skills.core.speckit.speckit_runner \\
        --project-path <path> --project-name <name> --instance-dir <dir> \\
        --context-file <goal-text-file>
"""

import datetime
import logging
from pathlib import Path
from typing import Optional, Tuple

from app.prompts import load_prompt_or_skill
from app.speckit_orchestration import extract_overrides, has_constitution

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).resolve().parent


def _progress(msg: str) -> None:
    """Print a timestamped progress line (captured into pending.md via /live)."""
    ts = datetime.datetime.now().strftime("%H:%M")
    print(f"{ts} — {msg}", flush=True)


def run_speckit(
    project_path: str,
    project_name: str,
    goal: str,
    instance_dir: str = "",
    base_branch: Optional[str] = None,
    notify_fn=None,
) -> Tuple[bool, str]:
    """Execute the ``/speckit`` pipeline for ``goal`` in the target project.

    Returns ``(success, summary)``.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    project_name = project_name or ""
    goal = (goal or "").strip()
    if not goal:
        msg = "No goal provided to /speckit."
        notify_fn(f"❌ {msg}")
        return False, msg

    # Defense-in-depth: the handler already gated on the constitution, but
    # re-check so a /speckit mission can never run without it (FR-003).
    if not has_constitution(project_path):
        msg = (
            f"❌ speckit abort: {project_name or project_path} has no constitution "
            "at .specify/memory/constitution.md."
        )
        notify_fn(msg)
        return False, msg

    from app.config import get_branch_prefix, get_speckit_config
    from app.projects_config import resolve_base_branch
    from app.pr_submit import guess_project_name

    cfg = get_speckit_config()
    # FR-007: a `branch:` token in the goal overrides the base branch; `repo:` is
    # informational (the project is already resolved). Tokens are stripped from
    # the goal text that goes into the prompt.
    _repo_token, branch_token, goal_text = extract_overrides(goal)
    goal_text = goal_text or goal
    effective_base = (
        base_branch
        or branch_token
        or resolve_base_branch(project_name or guess_project_name(project_path), project_path)
    )

    label = project_name or project_path
    _progress(f"Running /speckit for {label}: {goal_text[:80]}")
    notify_fn(f"📋 /speckit started for {label}: {goal_text[:120]}")

    prompt = load_prompt_or_skill(
        _SKILL_DIR,
        "speckit",
        GOAL=goal_text,
        PROJECT=label,
        BRANCH_PREFIX=get_branch_prefix(),
        BASE_BRANCH=effective_base,
        REVIEW_MAX_ITERATIONS=str(cfg["review_max_iterations"]),
        REVIEW_SEVERITY=cfg["review_severity"],
    )

    try:
        output = _invoke_claude(project_path, prompt)
    except Exception as exc:  # surface any CLI failure to the operator
        msg = f"❌ /speckit invocation failed for {label}: {str(exc)[:200]}"
        notify_fn(msg)
        return False, msg

    if not output:
        msg = f"⚠️ /speckit produced no output for {label}: {goal_text[:120]}"
        notify_fn(msg)
        return False, msg

    _progress("/speckit pipeline complete")
    notify_fn(f"✅ /speckit complete for {label}: {goal_text[:120]}")
    return True, output.strip()


def _invoke_claude(project_path: str, prompt: str) -> str:
    """Run the speckit orchestration prompt via the Claude CLI (mission tier)."""
    from app.claude_step import run_skill_loop
    from app.cli_provider import CLAUDE_TOOLS, run_command_streaming
    from app.config import get_skill_max_turns, get_skill_timeout

    def _step_fn(_evidence):
        return run_command_streaming(
            prompt,
            project_path,
            allowed_tools=sorted(CLAUDE_TOOLS),
            model_key="mission",
            max_turns=get_skill_max_turns(),
            timeout=get_skill_timeout(),
        )

    loop_outcome = run_skill_loop(
        step_fn=_step_fn,
        evidence_fn=lambda _a, _r: "",
        should_continue_fn=lambda _a, _r: (False, "done"),
        max_attempts=1,
    )

    attempts = loop_outcome.get("attempts", [])
    if attempts and attempts[0].get("error"):
        raise attempts[0]["error"]
    return attempts[0]["result"] if attempts else ""


def _read_context(context_file: Optional[str], context: Optional[str]) -> str:
    """Read the goal from ``--context-file`` (generic builder) or ``--context``."""
    if context_file:
        try:
            return Path(context_file).read_text(encoding="utf-8")
        except OSError:
            return ""
    return context or ""


def main(argv=None) -> int:
    """CLI entry point for speckit_runner."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the native /speckit pipeline.")
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--project-name", default="")
    parser.add_argument("--instance-dir", default="")
    parser.add_argument("--context-file", default=None)
    parser.add_argument("--context", default=None)
    parser.add_argument("--base-branch", default=None)
    cli = parser.parse_args(argv)

    goal = _read_context(cli.context_file, cli.context)
    success, summary = run_speckit(
        project_path=cli.project_path,
        project_name=cli.project_name,
        goal=goal,
        instance_dir=cli.instance_dir,
        base_branch=cli.base_branch,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
