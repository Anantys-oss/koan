"""Shared helpers for URL-oriented skill runners.

The argparse flags here are used by /plan, /deepplan, /fix, and /implement
to keep CLI behavior consistent with skill_dispatch command construction.
merge_context_with_base_branch() is the single source of truth for folding a
base-branch hint into planning context, shared by /plan and /deepplan.
"""

from __future__ import annotations

import argparse
from typing import Optional


def merge_context_with_base_branch(
    context: Optional[str], base_branch: Optional[str],
) -> str:
    """Merge user context with an optional base-branch hint for planning.

    Returns the trimmed context when no branch is given, the branch hint
    alone when there is no context, or both joined by a blank line.
    """
    context_text = (context or "").strip()
    branch_text = (base_branch or "").strip()
    if not branch_text:
        return context_text
    branch_hint = f"Target base branch: `{branch_text}`."
    if context_text:
        return f"{context_text}\n\n{branch_hint}"
    return branch_hint


def add_url_skill_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common URL-skill flags to a parser.

    Adds:
      - --context
      - --base-branch
      - --project-name
      - --instance-dir
    """
    parser.add_argument(
        "--context",
        help="Additional context for the skill run",
        default=None,
    )
    parser.add_argument(
        "--base-branch",
        help="Target base branch override (e.g. 'main' or '11.126')",
        default=None,
    )
    parser.add_argument(
        "--project-name",
        help="Koan project name for memory and tracker configuration",
        default="",
    )
    parser.add_argument(
        "--instance-dir",
        help="Koan instance directory for project memory",
        default="",
    )
