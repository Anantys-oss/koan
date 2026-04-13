"""External skill dispatch — invoke custom skill handlers from GitHub/Jira bridges.

Core skills (plan, rebase, review, …) have dedicated runner modules registered
in ``skill_dispatch._SKILL_RUNNERS`` and run as separate subprocesses driven by
the agent loop. Custom skills under ``instance/skills/<scope>/`` typically ship
a ``handler.py`` that is invoked in-process — exactly the path Telegram takes
via ``command_handlers._dispatch_skill``.

Without this helper, a GitHub/Jira @mention for a custom skill would queue a
``/cp_fix …`` slash mission that has no registered runner and no ``_runner.py``
file, so ``skill_dispatch.build_skill_command()`` would return None.

What this module does:

1. Decides whether a skill should be dispatched in-process (custom scope with
   a handler) or left to the existing slash-mission path (core skills and
   anything with an explicit ``_runner.py``).
2. Synthesizes a ``SkillContext`` that matches what Telegram passes to the
   same handler.
3. Auto-feeds the originating issue key into ``ctx.args`` when the author
   omitted it but the @mention was posted on a Jira issue, or on a GitHub
   issue/PR whose title or body contains a Jira key.

Handlers remain untouched — detection happens at the dispatch boundary.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from app.skills import Skill, SkillContext, SkillError, execute_skill

log = logging.getLogger(__name__)

# Matches Jira-style keys like ``CPANEL-123`` or ``FOO-9``.
# Kept loose (2+ letters, any uppercase prefix) so it works across projects.
_JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def _has_jira_key(text: str) -> bool:
    return bool(text) and bool(_JIRA_KEY_RE.search(text))


def _extract_jira_key(text: str) -> Optional[str]:
    if not text:
        return None
    match = _JIRA_KEY_RE.search(text)
    return match.group(0) if match else None


def should_dispatch_in_process(skill: Skill) -> bool:
    """Return True when the skill should be executed in-process by the bridge.

    We dispatch in-process for non-core skills that have a ``handler.py``.
    Core skills and anything with a dedicated ``_runner.py`` keep the existing
    slash-mission path — that path is well-exercised by /plan, /rebase, …
    """
    if not skill.has_handler():
        return False
    if skill.scope == "core":
        return False
    return True


def augment_args_with_issue_key(
    context: str,
    *,
    jira_issue_key: Optional[str] = None,
    github_title: Optional[str] = None,
    github_body: Optional[str] = None,
) -> str:
    """Append an originating Jira key to ``context`` when one is missing.

    Precedence when the author's context has no Jira key:
      1. ``jira_issue_key`` (Jira source — always authoritative).
      2. First Jira-style key found in the GitHub issue title.
      3. First Jira-style key found in the GitHub issue body.

    When the context already contains a Jira key we leave it alone so the
    author can override the source issue if they want.
    """
    context = (context or "").strip()
    if _has_jira_key(context):
        return context

    key = jira_issue_key
    if not key:
        key = _extract_jira_key(github_title or "")
    if not key:
        key = _extract_jira_key(github_body or "")

    if not key:
        return context

    if context:
        return f"{context} {key}"
    return key


def _resolve_instance_dir() -> Optional[Path]:
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        return None
    return Path(koan_root) / "instance"


def _resolve_koan_root() -> Optional[Path]:
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        return None
    return Path(koan_root)


def try_dispatch_custom_handler(
    skill: Skill,
    command_name: str,
    context: str,
    *,
    source: str,
    jira_issue_key: Optional[str] = None,
    github_title: Optional[str] = None,
    github_body: Optional[str] = None,
) -> Optional[str]:
    """Invoke a custom skill's handler in-process, mirroring the Telegram path.

    Args:
        skill: The resolved Skill object (already validated as github_enabled).
        command_name: The command the user typed (e.g. "cpfix").
        context: Free-form text the user appended after the command.
        source: Where the mention came from — ``"github"`` or ``"jira"``.
        jira_issue_key: The Jira issue key for Jira-sourced mentions.
        github_title: GitHub issue/PR title, used to auto-feed a Jira key.
        github_body:  GitHub issue/PR body, used to auto-feed a Jira key.

    Returns:
        ``None`` when the skill should fall through to the regular
        slash-mission path (core skills, prompt-only skills, or when KOAN_ROOT
        isn't configured). Otherwise returns the handler's reply text — which
        may be an empty string when the handler queued a mission and produced
        no user-visible reply.
    """
    if not should_dispatch_in_process(skill):
        return None

    instance_dir = _resolve_instance_dir()
    koan_root = _resolve_koan_root()
    if instance_dir is None or koan_root is None:
        log.warning(
            "external_skill_dispatch: KOAN_ROOT not set — falling back to "
            "slash-mission path for %s",
            skill.qualified_name,
        )
        return None

    augmented = augment_args_with_issue_key(
        context,
        jira_issue_key=jira_issue_key,
        github_title=github_title,
        github_body=github_body,
    )

    ctx = SkillContext(
        koan_root=koan_root,
        instance_dir=instance_dir,
        command_name=command_name,
        args=augmented,
    )

    log.info(
        "external_skill_dispatch: invoking %s from %s (args=%r)",
        skill.qualified_name, source, augmented,
    )

    result = execute_skill(skill, ctx)

    if isinstance(result, SkillError):
        log.error(
            "external_skill_dispatch: %s crashed: %s",
            skill.qualified_name, result.exception,
        )
        return result.message

    if result is None:
        return ""
    return str(result)
