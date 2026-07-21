"""
Kōan — Conventional commit runner.

Analyzes the project working tree, builds a structured commit prompt, and
invokes the CLI provider so Claude can stage relevant files and create a
conventional commit.

CLI:
    python3 -m skills.core.commit.commit_runner \
        --project-path <path> --project-name <name> --instance-dir <dir> \
        [--context-file <path>]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.prompts import load_skill_prompt


def _read_context_file(path: str) -> str:
    """Read optional free-text message hint from a context file."""
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        # Surface the failure so a lost hint is diagnosable (not silent).
        print(
            f"Warning: failed to read context file {path}: {exc}",
            file=sys.stderr,
        )
        return ""


def _git(project_path: str, args: list, timeout: int = 15) -> Tuple[int, str, str]:
    """Run a git command; return (rc, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _head_sha(project_path: str) -> Optional[str]:
    """Return the full HEAD SHA, or None if it cannot be resolved."""
    rc, out, _ = _git(project_path, ["rev-parse", "HEAD"])
    if rc != 0:
        return None
    sha = out.strip()
    return sha or None


def _preflight_git_state(project_path: str) -> Optional[str]:
    """Return an abort reason if the tree is not safe to commit, else None."""
    rc, branch, err = _git(project_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        return f"Not a git repository or git failed: {err.strip() or 'unknown error'}"

    branch = branch.strip()
    if branch in ("main", "master"):
        return (
            f"On protected base branch '{branch}' — refusing to commit. "
            "Switch to a feature branch first."
        )

    rc, conflicts, err = _git(project_path, ["diff", "--name-only", "--diff-filter=U"])
    if rc != 0:
        return (
            "Failed to check for merge conflicts: "
            f"{err.strip() or 'unknown error'}"
        )
    if conflicts.strip():
        files = ", ".join(conflicts.strip().splitlines()[:5])
        return f"Unresolved merge conflicts: {files}"

    rc, status, _ = _git(project_path, ["status", "--porcelain"])
    if rc != 0:
        return "Failed to read git status."
    if not status.strip():
        return "Working tree clean — nothing to commit."

    return None


def build_commit_prompt(
    project_name: str,
    message_hint: str = "",
    skill_dir: Optional[Path] = None,
    project_path: Optional[str] = None,
) -> str:
    """Build the conventional-commit prompt from the skill template."""
    if skill_dir is None:
        skill_dir = Path(__file__).resolve().parent

    hint_block = message_hint.strip() if message_hint else "(none — infer from the diff)"

    return load_skill_prompt(
        skill_dir,
        "commit",
        project_path=project_path,
        PROJECT_NAME=project_name,
        MESSAGE_HINT=hint_block,
    )


def _run_claude_commit(prompt: str, project_path: str) -> str:
    """Invoke Claude with tools needed for git inspection and commit."""
    from app.cli_provider import run_command_streaming
    from app.config import get_skill_max_turns, get_skill_timeout

    # Bash is required for git; Read/Glob/Grep help inspect surrounding context.
    return run_command_streaming(
        prompt,
        project_path,
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        model_key="mission",
        max_turns=get_skill_max_turns(),
        timeout=get_skill_timeout(),
    )


def run_commit(
    project_path: str,
    project_name: str,
    instance_dir: str = "",  # noqa: ARG001 — kept for generic runner CLI parity
    message_hint: str = "",
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute the conventional-commit pipeline.

    Returns:
        (success, summary) tuple. Success is True only when HEAD advances
        (a new commit is positively confirmed), never from model text alone.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    abort = _preflight_git_state(project_path)
    if abort:
        msg = f"❌ Commit aborted for {project_name}: {abort}"
        notify_fn(msg)
        return False, abort

    head_before = _head_sha(project_path)
    if not head_before:
        err = "Failed to read HEAD before commit."
        notify_fn(f"❌ Commit aborted for {project_name}: {err}")
        return False, err

    hint_note = f" (hint: {message_hint})" if message_hint else ""
    notify_fn(f"📝 Creating conventional commit for {project_name}{hint_note}...")

    if skill_dir is None:
        skill_dir = Path(__file__).resolve().parent

    prompt = build_commit_prompt(
        project_name=project_name,
        message_hint=message_hint,
        skill_dir=skill_dir,
        project_path=project_path,
    )

    try:
        raw_output = _run_claude_commit(prompt, project_path)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        err = f"Commit failed: {exc}"
        notify_fn(f"❌ {err}")
        return False, err

    if not raw_output or not raw_output.strip():
        err = f"Commit produced no output for {project_name}."
        notify_fn(f"❌ {err}")
        return False, err

    from app.text_utils import clean_cli_response

    report = clean_cli_response(raw_output).strip()

    # Only treat the run as success when HEAD actually advanced. Model tokens
    # like COMMITTED / ABORTED are informative for the summary, not proof.
    head_after = _head_sha(project_path)
    committed = bool(head_after and head_after != head_before)

    if committed:
        rc, log_line, _ = _git(project_path, ["log", "-1", "--oneline"])
        log_part = log_line.strip() if rc == 0 and log_line.strip() else head_after[:12]
        summary = f"Commit for {project_name}: {log_part}\n\n{report}"
        from app.messaging_level import notify_outcome

        notify_outcome(f"📝 {summary}", notify_fn)
        return True, summary

    # HEAD unchanged — fail closed for abort text, truncated output, or false claims.
    if report.lstrip().startswith("ABORTED") or "\nABORTED" in report[:200]:
        summary = f"Commit result for {project_name}:\n\n{report}"
    else:
        summary = (
            f"Commit failed for {project_name}: HEAD unchanged "
            f"(no new commit created).\n\n{report}"
        )
    notify_fn(f"❌ {summary}")
    return False, summary


def main(argv=None) -> int:
    """CLI entry point for commit_runner."""
    parser = argparse.ArgumentParser(
        description="Create a conventional commit from the working tree.",
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Project name for labeling",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to instance directory",
    )
    parser.add_argument(
        "--context-file", default="",
        help="Optional file with free-text message hint",
    )
    cli_args = parser.parse_args(argv)

    message_hint = _read_context_file(cli_args.context_file)
    # Context file may still contain a leading project name from the mission
    # text; strip it when it matches the resolved project name.
    if message_hint:
        tokens = message_hint.split(None, 1)
        if tokens and tokens[0].lower() == cli_args.project_name.lower():
            message_hint = tokens[1] if len(tokens) > 1 else ""

    skill_dir = Path(__file__).resolve().parent
    try:
        success, summary = run_commit(
            project_path=cli_args.project_path,
            project_name=cli_args.project_name,
            instance_dir=cli_args.instance_dir,
            message_hint=message_hint,
            skill_dir=skill_dir,
        )
    except Exception as exc:  # noqa: BLE001 — top-level CLI guard
        print(f"Commit failed: {exc}", file=sys.stderr)
        return 1

    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
