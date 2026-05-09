"""Kōan — Caveman output optimization helpers.

Single source of truth for "should the caveman directive be appended to this
prompt?".  The directive (no filler, 3-6 word sentences, direct answers) lives
in ``koan/system-prompts/caveman-mode.md`` and is injected at three Claude
entry points:

- The agent loop (``app.prompt_builder._get_caveman_section``)
- Skill runners loaded via ``app.prompts.load_prompt_or_skill`` /
  ``app.prompts.load_skill_prompt``
- The chat handler (``app.awake._build_chat_prompt``)

Per-skill semantics are **opt-in**: skills do not get caveman unless they
explicitly request it.  A skill opts in via either:

1. ``caveman: true`` in its ``SKILL.md`` frontmatter (skill author declares
   the skill benefits from terse output), or
2. The skill's canonical name is listed in
   ``optimizations.caveman.include`` in ``config.yaml`` (instance owner
   overrides).

The agent loop (regular missions, no associated skill) is gated only by the
global ``optimizations.caveman.enabled`` flag — this preserves the
high-volume token savings shipped in PR #1279.

Aliases (e.g. ``deeplan``) are resolved to canonical names (``deepplan``)
before matching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def _read_skill_caveman_flag(skill_dir: Path) -> Optional[bool]:
    """Return the SKILL.md ``caveman:`` frontmatter value, or None if absent."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        # Reuse the SKILL.md parser so we stay in sync with the registry.
        from app.skills import parse_skill_md
        skill = parse_skill_md(skill_md)
    except Exception as e:
        import sys
        print(f"[caveman] failed to parse {skill_md}: {e}", file=sys.stderr)
        return None
    if skill is None:
        return None
    return getattr(skill, "caveman_enabled", None)


def is_skill_included(
    skill_name: Optional[str],
    skill_dir: Optional[Path] = None,
) -> bool:
    """Return True when this skill has opted **in** to caveman.

    Resolution order — operator config wins so an instance owner can override
    a skill author's default without forking the skill:

    1. If the skill's canonical name is in
       ``optimizations.caveman.include`` → True.
    2. Else if the SKILL.md frontmatter declares ``caveman: true`` → True.
    3. Otherwise → False.

    Args:
        skill_name: Skill command name as typed by the user (e.g. ``"plan"``,
            ``"deeplan"``).  Required for config-list matching.
        skill_dir: Path to the skill directory.  Required for SKILL.md flag
            inspection.
    """
    if skill_name:
        from app.config import get_caveman_include_list
        from app.skill_dispatch import _resolve_canonical
        canonical = _resolve_canonical(skill_name)
        if canonical in get_caveman_include_list():
            return True

    if skill_dir is not None:
        flag = _read_skill_caveman_flag(skill_dir)
        if flag is True:
            return True

    return False


def get_caveman_section(
    skill_name: Optional[str] = None,
    skill_dir: Optional[Path] = None,
) -> str:
    """Return the caveman directive text, or ``""`` when it should be suppressed.

    Two distinct gates:

    - **Agent loop / unspecified context** (``skill_name`` is None and
      ``skill_dir`` is None): governed only by the global enabled flag.
      Preserves PR #1279's default token savings on every regular mission.
    - **Skill or chat context** (either argument provided): also requires
      :func:`is_skill_included` to return True (config ``include`` list or
      SKILL.md ``caveman: true``).
    """
    from app.config import is_caveman_mode
    if not is_caveman_mode():
        return ""

    skill_context = skill_name is not None or skill_dir is not None
    if skill_context and not is_skill_included(skill_name, skill_dir):
        return ""

    try:
        from app.prompts import load_prompt
        return load_prompt("caveman-mode")
    except (OSError, FileNotFoundError):
        return ""


def append_caveman(
    prompt: str,
    skill_name: Optional[str] = None,
    skill_dir: Optional[Path] = None,
) -> str:
    """Return ``prompt`` with the caveman directive appended when applicable."""
    section = get_caveman_section(skill_name=skill_name, skill_dir=skill_dir)
    if not section:
        return prompt
    sep = "" if prompt.endswith("\n") else "\n\n"
    return f"{prompt}{sep}{section}"
