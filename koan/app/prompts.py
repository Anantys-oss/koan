"""Kōan — System prompt loader.

Loads prompt templates from koan/system-prompts/ and substitutes placeholders.
Supports ``{@include partial-name}`` directives for composable prompt fragments.
"""

import logging
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

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


_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _substitute(template: str, kwargs: dict) -> str:
    """Replace {KEY} placeholders in a template string (single pass).

    A single regex pass is used deliberately: a value substituted for one
    placeholder is NOT re-scanned for other placeholders. This prevents a value
    (e.g. an operator goal, or untrusted issue text once /speckit supports issue
    triggers) that happens to contain literal ``{OTHER_KEY}`` text from
    contaminating or mangling other substitutions — a prompt-injection /
    integrity concern (constitution Principle V).
    """
    values = _default_placeholders()
    values.update(kwargs)

    def _replace(match):
        key = match.group(1)
        return str(values[key]) if key in values else match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, template)


def _default_placeholders() -> dict:
    """Placeholders that every prompt rendered through this module gets.

    Default placeholders are merged with caller-supplied kwargs in
    :func:`_substitute` and applied to every prompt that flows through
    :func:`load_prompt`, :func:`load_skill_prompt`, and
    :func:`load_prompt_or_skill`. Any future caller that concatenates raw
    prompt markdown without going through these helpers will *not* get the
    substitution — and the literal ``{KOAN_PYTHON}`` token would land in
    Claude's prompt and execute as a shell command. The regression test
    :class:`TestDefaultPlaceholdersAlwaysResolved` in ``test_prompts.py``
    guards against this by walking every system + skill prompt.

    Keys currently injected:
        * ``KOAN_PYTHON`` — quoted absolute path to the Python interpreter
          running this process, so prompts can advise Claude to invoke
          ``{KOAN_PYTHON} -m app.issue_cli ...`` and inherit the same venv.
    """
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


def load_skill_prompt(
    skill_dir: Path, name: str, project_path: Optional[str] = None, **kwargs: str
) -> str:
    """Load a prompt from a skill's prompts/ directory.

    Looks for ``skill_dir/prompts/<name>.md`` first, then falls back to
    the global ``system-prompts/`` directory for safe incremental migration.

    The caveman directive (``optimizations.caveman``) is appended automatically
    when the skill is not opted out — see :mod:`app.caveman` for resolution
    rules.

    When ``project_path`` is supplied and the target repo ships
    ``<project_path>/.koan/skills/<skill>/*.md`` steering files, those are framed
    and appended to the built-in prompt (append-only) — see
    :func:`_maybe_append_project_skill_instructions`.

    Args:
        skill_dir: Path to the skill directory (e.g. ``skills/core/plan``).
        name: Prompt file name without .md extension.
        project_path: Target project checkout; enables ``.koan/skills/`` reads.
            Default ``None`` keeps every existing caller byte-identical.
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
    prompt = _maybe_append_caveman(prompt, skill_dir)
    prompt = _maybe_append_project_skill_instructions(prompt, skill_dir, project_path)
    # General KOAN.md rides BELOW the per-skill block: precedence is
    # `.koan/skills/<skill>/* > KOAN.md` (see specs/components/skills.md).
    return _maybe_append_general_koan_md(prompt, skill_dir, project_path)


def load_prompt_or_skill(
    skill_dir: Optional[Path], name: str,
    project_path: Optional[str] = None, **kwargs: str,
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
        project_path: Target project checkout; threaded to
            :func:`load_skill_prompt` for ``.koan/skills/`` reads. Default
            ``None`` is a no-op.
        **kwargs: Placeholder values to substitute.

    Returns:
        The prompt string with placeholders replaced.
    """
    if skill_dir is not None:
        return load_skill_prompt(skill_dir, name, project_path=project_path, **kwargs)
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


def _maybe_append_project_skill_instructions(
    prompt: str, skill_dir: Path, project_path: Optional[str],
) -> str:
    """Append the project's ``.koan/skills/<name>/`` instructions, when present.

    No-op unless ``skill_dir`` has a ``SKILL.md`` AND ``project_path`` is set —
    the same real-skill-package gate as :func:`_maybe_append_caveman`, so
    arbitrary directory paths (tests, legacy callers) stay untouched and the
    default ``project_path=None`` keeps every existing call byte-identical.

    Failures are swallowed (the append is additive guidance, not a correctness
    feature); they surface via the module logger so silent regressions stay
    visible in the daemon log.
    """
    try:
        if not project_path or not (skill_dir / "SKILL.md").is_file():
            return prompt
        from app.project_koan import log_context_load, read_skill_instructions
        content = read_skill_instructions(project_path, skill_dir.name)
        if not content:
            return prompt
        log_context_load(f".koan/skills/{skill_dir.name}", content)
        block = load_prompt(
            "koan-skill",
            SKILL_NAME=skill_dir.name,
            KOAN_SKILL_CONTENT=content,
        )
        return f"{prompt}\n\n{block}"
    except Exception as e:
        logger.warning(".koan skill injection failed for %s: %s", skill_dir, e)
        return prompt


def _maybe_append_general_koan_md(
    prompt: str, skill_dir: Path, project_path: Optional[str],
) -> str:
    """Append the project's general ``KOAN.md`` (root + ``.koan/KOAN.md``), when present.

    Mirrors :func:`_maybe_append_project_skill_instructions`' gating: a no-op unless
    ``skill_dir`` has a ``SKILL.md`` AND ``project_path`` is set, so arbitrary
    directory paths (tests, legacy callers) stay untouched and the default
    ``project_path=None`` keeps every existing call byte-identical. Framed via the
    shared ``koan-md`` template — the same framing the agent loop uses in
    ``prompt_builder._get_koan_md_section`` — and appended AFTER the
    ``.koan/skills/<name>/`` block so the precedence reads
    ``.koan/skills/<skill>/* > KOAN.md``. A successful injection is announced via
    ``project_koan.log_context_load`` (stderr → ``logs/run.log``) so ``make logs``
    shows the load.

    Failures are swallowed (additive guidance, not a correctness feature); they
    surface via the module logger so silent regressions stay visible in the log.
    """
    try:
        if not project_path or not (skill_dir / "SKILL.md").is_file():
            return prompt
        from app.project_koan import log_context_load, read_general_koan_md
        content = read_general_koan_md(project_path)
        if not content:
            return prompt
        log_context_load("KOAN.md", content)
        block = load_prompt("koan-md", KOAN_MD_CONTENT=content)
        return f"{prompt}\n\n{block}"
    except Exception as e:
        logger.warning("general KOAN.md injection failed for %s: %s", skill_dir, e)
        return prompt
