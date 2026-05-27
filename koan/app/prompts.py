"""Kōan — System prompt loader.

Loads prompt templates from koan/system-prompts/ and substitutes placeholders.
Supports ``{@include partial-name}`` directives for composable prompt fragments.
"""

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROMPT_DIR = Path(__file__).parent.parent / "system-prompts"
PARTIALS_DIR_NAME = "_partials"
_INCLUDE_RE = re.compile(r"^\{@include\s+([\w-]+)\}\s*$", re.MULTILINE)
_MAX_INCLUDE_DEPTH = 3


def get_prompt_path(name: str) -> Path:
    """Return the full path to a system prompt file.

    Args:
        name: Prompt file name without .md extension (e.g. "chat", "pick-mission")

    Returns:
        Path to the prompt file (e.g. koan/system-prompts/chat.md)
    """
    return PROMPT_DIR / f"{name}.md"


def _read_prompt_with_git_fallback(path: Path) -> str:
    """Read a prompt file, falling back to git if the file is missing on disk.

    When Kōan works on its own repo and a rebase or crash leaves the tree on a
    PR branch, prompt files added after that branch was created may be absent.
    This helper tries ``upstream/main`` then ``origin/main`` via ``git show``.
    """
    try:
        return path.read_text()
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise FileNotFoundError(path)
        root = Path(result.stdout.strip())
        rel_path = path.relative_to(root)
    except (subprocess.TimeoutExpired, ValueError) as e:
        raise FileNotFoundError(path) from e

    for remote in ("upstream/main", "origin/main"):
        try:
            result = subprocess.run(
                ["git", "show", f"{remote}:{rel_path}"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout
        except subprocess.TimeoutExpired:
            continue

    raise FileNotFoundError(path)


def _resolve_includes(
    template: str,
    skill_dir: Optional[Path] = None,
    _depth: int = 0,
) -> str:
    """Resolve ``{@include partial-name}`` directives in *template*.

    Resolution order for each partial:
    1. ``<skill_dir>/prompts/_partials/<name>.md`` (skill-local override)
    2. ``koan/system-prompts/_partials/<name>.md`` (global default)

    Includes are resolved recursively up to ``_MAX_INCLUDE_DEPTH`` levels.
    Missing partials are left as-is so downstream placeholder substitution
    or the caller can decide how to handle them.
    """
    if _depth >= _MAX_INCLUDE_DEPTH:
        return template

    def _replace_match(match: re.Match) -> str:
        name = match.group(1)
        # Try skill-local partials first
        if skill_dir is not None:
            skill_partial = skill_dir / "prompts" / PARTIALS_DIR_NAME / f"{name}.md"
            if skill_partial.is_file():
                content = skill_partial.read_text().strip()
                return _resolve_includes(content, skill_dir, _depth + 1)
        # Fall back to global partials
        global_partial = PROMPT_DIR / PARTIALS_DIR_NAME / f"{name}.md"
        if global_partial.is_file():
            content = global_partial.read_text().strip()
            return _resolve_includes(content, skill_dir, _depth + 1)
        # Partial not found — leave the directive as-is
        return match.group(0)

    return _INCLUDE_RE.sub(_replace_match, template)


def _substitute(template: str, kwargs: dict) -> str:
    """Replace {KEY} placeholders in a template string."""
    values = _default_placeholders()
    values.update(kwargs)
    for key, value in values.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


def _default_placeholders() -> dict:
    return {"KOAN_PYTHON": shlex.quote(sys.executable or "python3")}


def load_prompt(name: str, **kwargs: str) -> str:
    """Load a system prompt template and substitute placeholders.

    Args:
        name: Prompt file name without .md extension (e.g. "chat", "format-message")
        **kwargs: Placeholder values to substitute. Keys map to {KEY} in the template.

    Returns:
        The prompt string with placeholders replaced.
    """
    template = _read_prompt_with_git_fallback(get_prompt_path(name))
    template = _resolve_includes(template)
    return _substitute(template, kwargs)


def load_skill_prompt(skill_dir: Path, name: str, **kwargs: str) -> str:
    """Load a prompt from a skill's prompts/ directory.

    Looks for ``skill_dir/prompts/<name>.md`` first, then falls back to
    the global ``system-prompts/`` directory for safe incremental migration.

    The caveman directive (``optimizations.caveman``) is appended automatically
    when the skill is not opted out — see :mod:`app.caveman` for resolution
    rules.

    Args:
        skill_dir: Path to the skill directory (e.g. ``skills/core/plan``).
        name: Prompt file name without .md extension.
        **kwargs: Placeholder values to substitute. Keys map to {KEY} in the template.

    Returns:
        The prompt string with placeholders replaced.
    """
    skill_prompt = skill_dir / "prompts" / f"{name}.md"
    try:
        template = _read_prompt_with_git_fallback(skill_prompt)
    except FileNotFoundError:
        # Skill prompt not found even via git — fall back to system-prompts/
        template = _read_prompt_with_git_fallback(get_prompt_path(name))
    template = _resolve_includes(template, skill_dir=skill_dir)
    prompt = _substitute(template, kwargs)
    return _maybe_append_caveman(prompt, skill_dir)


def load_prompt_or_skill(
    skill_dir: Optional[Path], name: str, **kwargs: str
) -> str:
    """Load a prompt, preferring the skill directory when available.

    Consolidates the repeated pattern::

        if skill_dir is not None:
            prompt = load_skill_prompt(skill_dir, name, **kw)
        else:
            prompt = load_prompt(name, **kw)

    When a ``skill_dir`` is supplied, the caveman directive is auto-appended
    via :func:`load_skill_prompt`.  When it's ``None`` the caller is the agent
    loop (or a system-prompt consumer) and is expected to inject caveman
    itself if appropriate.

    Args:
        skill_dir: Path to the skill directory, or None for system prompts.
        name: Prompt file name without .md extension.
        **kwargs: Placeholder values to substitute.

    Returns:
        The prompt string with placeholders replaced.
    """
    if skill_dir is not None:
        return load_skill_prompt(skill_dir, name, **kwargs)
    return load_prompt(name, **kwargs)


def _maybe_append_caveman(prompt: str, skill_dir: Path) -> str:
    """Append the caveman directive when the skill at ``skill_dir`` opts in.

    Only fires when ``skill_dir`` actually contains a ``SKILL.md`` — that
    keeps the behaviour of arbitrary directory paths (used in some tests and
    legacy callers) untouched, and limits injection to real skill packages.

    Failures are swallowed: caveman is an optimization, not a correctness
    feature, and a faulty config or import error must not break prompt loads.
    Any failure surfaces to stderr so silent regressions stay visible.
    """
    try:
        if not (skill_dir / "SKILL.md").is_file():
            return prompt
        from app.caveman import append_caveman
        return append_caveman(prompt, skill_name=skill_dir.name, skill_dir=skill_dir)
    except Exception as e:
        import sys
        print(f"[prompts] caveman injection failed for {skill_dir}: {e}",
              file=sys.stderr)
        return prompt
