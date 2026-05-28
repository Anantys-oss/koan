"""Auto-generate structured PR descriptions from a git diff.

Public API
----------
describe_pr(project_path, base_branch) -> dict | None
    Returns {"summary": list[str], "why": str, "how": list[str],
    "testing": list[str], "limitations": list[str]} or None when the diff
    is empty or generation fails.

format_description(desc) -> str
    Render the parsed dict as a markdown string suitable for a PR body section.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


# Maximum diff size sent to Claude (characters, not tokens — rough proxy).
# Keeps context well within Haiku's window for typical PRs.
_MAX_DIFF_CHARS = 32_000


def _run_git(args: list, cwd: str, timeout: int = 30) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=cwd, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def _get_diff(project_path: str, base_branch: str) -> str:
    """Return git diff between base_branch and HEAD, truncated if huge."""
    try:
        # Stat header first (always preserved)
        stat = _run_git(
            ["git", "diff", "--stat", f"{base_branch}...HEAD"],
            cwd=project_path,
        ).strip()
        # Full diff
        full = _run_git(
            ["git", "diff", f"{base_branch}...HEAD"],
            cwd=project_path,
        )
    except Exception as e:
        logger.warning("git diff failed: %s", e)
        return ""

    if not full.strip():
        return ""

    if len(full) > _MAX_DIFF_CHARS:
        truncated = full[:_MAX_DIFF_CHARS]
        return f"{stat}\n\n{truncated}\n\n[diff truncated]"

    return f"{stat}\n\n{full}"


def _get_log(project_path: str, base_branch: str) -> str:
    """Return compact commit log between base_branch and HEAD."""
    try:
        return _run_git(
            ["git", "log", f"{base_branch}..HEAD", "--format=- %s"],
            cwd=project_path,
        ).strip()
    except Exception as e:
        logger.warning("git log failed: %s", e)
        return ""


def _parse_description(raw: str) -> dict:
    """Parse Claude's markdown output into a structured dict.

    Handles leading prose before the first ## header, missing sections,
    and extra whitespace.

    Returns {"summary": list[str], "why": str, "how": list[str],
    "testing": list[str], "limitations": list[str]}.
    """
    # Drop everything before the first ## heading
    first_header = raw.find("## ")
    if first_header > 0:
        raw = raw[first_header:]

    sections: dict[str, list[str]] = {}
    current: Optional[str] = None

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(stripped)

    def bullets(key: str) -> list[str]:
        lines = sections.get(key, [])
        return [l[2:].strip() for l in lines if l.startswith("- ")]

    def prose(key: str) -> str:
        lines = sections.get(key, [])
        return " ".join(l for l in lines if l).strip()

    summary = bullets("summary")
    why = prose("why")
    how = bullets("how")
    testing = bullets("testing")
    limitations = bullets("limitations & risk")

    return {
        "summary": summary,
        "why": why,
        "how": how,
        "testing": testing,
        "limitations": limitations,
    }


def format_description(desc: dict) -> str:
    """Render a parsed description dict as a markdown PR body section."""
    parts: list[str] = []

    if desc.get("summary"):
        parts.append("## Summary\n")
        parts.extend(f"- {item}" for item in desc["summary"])
        parts.append("")

    if desc.get("why"):
        parts.append("## Why\n")
        parts.append(desc["why"])
        parts.append("")

    if desc.get("how"):
        parts.append("## How\n")
        parts.extend(f"- {item}" for item in desc["how"])
        parts.append("")

    if desc.get("testing"):
        parts.append("## Testing\n")
        parts.extend(f"- {item}" for item in desc["testing"])
        parts.append("")

    if desc.get("limitations"):
        parts.append("## Limitations & Risk\n")
        parts.extend(f"- {item}" for item in desc["limitations"])
        parts.append("")

    return "\n".join(parts).strip()


def describe_pr(project_path: str, base_branch: str) -> Optional[dict]:
    """Generate a structured PR description by diffing branch against base.

    Returns a dict with keys ``summary``, ``why``, ``how``, ``testing``,
    ``limitations`` on success, or ``None`` if the diff is empty or Claude
    is unavailable.
    """
    diff = _get_diff(project_path, base_branch)
    if not diff.strip():
        return None

    log = _get_log(project_path, base_branch)

    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_prompt

    prompt = load_prompt("describe-pr", DIFF=diff, LOG=log or "(none)")
    models = get_model_config()

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    from app.cli_exec import run_cli_with_retry

    try:
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=90, cwd=project_path,
        )
    except Exception as e:
        logger.warning("CLI call failed: %s", e)
        return None

    if result.returncode != 0:
        logger.warning("CLI returned %d: %s", result.returncode, result.stderr[:200])
        return None

    raw = result.stdout.strip()
    if not raw:
        return None

    parsed = _parse_description(raw)

    # Validate: at least one section must have data
    if not parsed["summary"] and not parsed["why"] and not parsed["how"]:
        logger.warning("all sections empty in parsed output")
        return None

    return parsed
