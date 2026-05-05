"""Kōan -- Skills system.

Loads skills from SKILL.md files, parses YAML frontmatter, and dispatches
commands to the appropriate handler (Python function or Claude prompt).

Directory layout:
    skills/<scope>/<skill-name>/SKILL.md     — skill definition
    skills/<scope>/<skill-name>/handler.py   — optional Python handler

SKILL.md format:
    ---
    name: status
    description: Show Kōan status
    version: 1.0.0
    audience: bridge        # bridge | agent | command | hybrid
    commands:
      - name: status
        description: Quick status overview
        aliases: [st]
      - name: ping
        description: Check run loop liveness
    handler: handler.py   # optional, defaults to prompt-based
    ---

    # Prompt body (used when no handler.py)
    ...
"""

import importlib
import importlib.util
import logging
import re
import subprocess
import sys
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

# Returned by _execute_handler() on unhandled exceptions so callers can
# distinguish handler crashes from intentional error responses.
SkillError = namedtuple("SkillError", ["skill_name", "exception", "message"])

_log = logging.getLogger(__name__)

# Valid audience values for skills.
# - "bridge": Telegram-only (process control, quick checks)
# - "agent": Exposed to Claude CLI as a plugin skill (auto-triggered by context)
# - "command": Exposed to Claude CLI as a slash command (explicit invocation)
# - "hybrid": Available in both Telegram and Claude CLI
VALID_AUDIENCES = ("bridge", "agent", "command", "hybrid")
DEFAULT_AUDIENCE = "bridge"


@dataclass
class SkillCommand:
    """A single command exposed by a skill."""

    name: str
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    usage: str = ""


@dataclass
class Skill:
    """A loaded skill definition."""

    name: str
    scope: str
    description: str = ""
    version: str = "0.0.0"
    commands: List[SkillCommand] = field(default_factory=list)
    handler_path: Optional[Path] = None
    prompt_body: str = ""
    skill_dir: Optional[Path] = None
    worker: bool = False
    audience: str = DEFAULT_AUDIENCE
    github_enabled: bool = False
    github_context_aware: bool = False
    cli_skill: Optional[str] = None
    group: str = ""
    emoji: str = ""
    requirements: List[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.scope}.{self.name}"

    def has_handler(self) -> bool:
        return self.handler_path is not None and self.handler_path.exists()


# ---------------------------------------------------------------------------
# SKILL.md parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


def _parse_yaml_lite(text: str) -> Dict[str, Any]:
    """Minimal YAML-subset parser for SKILL.md frontmatter.

    Handles:
      - key: value (strings, numbers)
      - key: [item1, item2] (inline lists)
      - commands: (block list of dicts with - name:/description:/aliases:)

    This avoids requiring PyYAML as a dependency for the core skills system.
    """
    result: Dict[str, Any] = {}
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            i += 1
            continue

        # Top-level key: value
        match = re.match(r"^(\w[\w_-]*)\s*:\s*(.*)", line)
        if not match:
            i += 1
            continue

        key = match.group(1)
        value = match.group(2).strip()

        if key == "commands" and not value:
            # Block list of command dicts
            commands = []
            i += 1
            current_cmd: Dict[str, Any] = {}
            while i < len(lines):
                cline = lines[i].rstrip()
                if not cline.startswith(" ") and not cline.startswith("\t"):
                    break
                cline = cline.strip()
                if cline.startswith("- name:"):
                    if current_cmd:
                        commands.append(current_cmd)
                    current_cmd = {"name": cline[7:].strip()}
                elif cline.startswith("description:"):
                    current_cmd["description"] = cline[12:].strip()
                elif cline.startswith("usage:"):
                    current_cmd["usage"] = cline[6:].strip()
                elif cline.startswith("aliases:"):
                    aliases_str = cline[8:].strip()
                    current_cmd["aliases"] = _parse_inline_list(aliases_str)
                i += 1
            if current_cmd:
                commands.append(current_cmd)
            result["commands"] = commands
            continue

        # Inline list: [item1, item2]
        if value.startswith("[") and value.endswith("]"):
            result[key] = _parse_inline_list(value)
        else:
            result[key] = value

        i += 1

    return result


def _parse_inline_list(s: str) -> List[str]:
    """Parse [item1, item2] into a list of strings."""
    s = s.strip()
    if s.startswith("["):
        s = s[1:]
    if s.endswith("]"):
        s = s[:-1]
    if not s.strip():
        return []
    return [item.strip().strip("'\"") for item in s.split(",") if item.strip()]


def _parse_bool_flag(meta: Dict[str, Any], key: str) -> bool:
    """Parse a boolean flag from SKILL.md frontmatter metadata.
    
    Accepts: "true", "yes", "1" (case-insensitive) as truthy values.
    Returns False for any other value or if key is missing.
    """
    return meta.get(key, "").lower() in ("true", "yes", "1")


def parse_skill_md(path: Path) -> Optional[Skill]:
    """Parse a SKILL.md file into a Skill object.

    Returns None if the file can't be parsed.
    """
    try:
        content = path.read_text()
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None

    frontmatter_text = match.group(1)
    prompt_body = match.group(2).strip()

    meta = _parse_yaml_lite(frontmatter_text)

    if "name" not in meta:
        return None

    # Parse commands
    commands = []
    for cmd_data in meta.get("commands", []):
        if isinstance(cmd_data, dict) and "name" in cmd_data:
            commands.append(
                SkillCommand(
                    name=cmd_data["name"],
                    description=cmd_data.get("description", ""),
                    aliases=cmd_data.get("aliases", []),
                    usage=cmd_data.get("usage", ""),
                )
            )

    # Resolve handler path (always record declared path; has_handler() checks existence)
    handler_path = None
    handler_name = meta.get("handler", "")
    if handler_name:
        handler_path = path.parent / handler_name

    skill_dir = path.parent

    # Parse boolean flags
    worker = _parse_bool_flag(meta, "worker")
    github_enabled = _parse_bool_flag(meta, "github_enabled")
    github_context_aware = _parse_bool_flag(meta, "github_context_aware")

    # Parse audience (default: "bridge" for backward compatibility)
    audience = meta.get("audience", DEFAULT_AUDIENCE).lower()
    if audience not in VALID_AUDIENCES:
        audience = DEFAULT_AUDIENCE

    # Parse cli_skill (optional provider slash command name)
    cli_skill = meta.get("cli_skill") or None

    # Parse group (for /help grouping)
    group = meta.get("group", "")

    # Parse emoji (for /list display)
    emoji = meta.get("emoji", "")

    # Parse requirements (for auto-install)
    requirements_raw = meta.get("requirements", [])
    if isinstance(requirements_raw, str):
        requirements_raw = [requirements_raw] if requirements_raw else []
    requirements = [r for r in requirements_raw if r]

    return Skill(
        name=meta["name"],
        scope=meta.get("scope", skill_dir.parent.name),
        description=meta.get("description", ""),
        version=meta.get("version", "0.0.0"),
        commands=commands,
        handler_path=handler_path,
        prompt_body=prompt_body,
        skill_dir=skill_dir,
        worker=worker,
        audience=audience,
        github_enabled=github_enabled,
        github_context_aware=github_context_aware,
        cli_skill=cli_skill,
        group=group,
        emoji=emoji,
        requirements=requirements,
    )


# ---------------------------------------------------------------------------
# Skill Registry
# ---------------------------------------------------------------------------

class SkillRegistry:
    """Discovers and manages skills from a directory tree.

    Expected layout:
        skills_dir/<scope>/<skill-name>/SKILL.md
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        self._skills: Dict[str, Skill] = {}  # key: "scope.name"
        self._command_map: Dict[str, Skill] = {}  # key: command name -> skill
        if skills_dir and skills_dir.is_dir():
            self._discover(skills_dir)

    def _discover(self, skills_dir: Path) -> None:
        """Scan directory tree for SKILL.md files."""
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            skill = parse_skill_md(skill_md)
            if skill is None:
                continue
            self._register(skill)

    def _register(self, skill: Skill) -> None:
        """Register a skill and build command lookup."""
        key = skill.qualified_name

        # Reject individual commands/aliases whose names contain hyphens.
        # Hyphens break Telegram command parsing (treated as word boundary).
        # See CLAUDE.md "No hyphens in skill names or aliases".
        # Only the offending command/alias is skipped — the rest of the skill
        # is still registered.
        valid_commands: List[SkillCommand] = []
        for cmd in skill.commands:
            if "-" in cmd.name:
                _log.error(
                    "Skill %s: command '%s' contains a hyphen — "
                    "skipping this command. Use underscores instead.",
                    key, cmd.name,
                )
                continue
            # Filter out hyphenated aliases, keep the rest
            bad_aliases = [a for a in cmd.aliases if "-" in a]
            if bad_aliases:
                _log.error(
                    "Skill %s: alias(es) %s contain a hyphen — "
                    "skipping these aliases. Use underscores instead.",
                    key, ", ".join(repr(a) for a in bad_aliases),
                )
            clean_aliases = [a for a in cmd.aliases if "-" not in a]
            valid_commands.append(SkillCommand(
                name=cmd.name,
                description=cmd.description,
                aliases=clean_aliases,
                usage=cmd.usage,
            ))

        self._skills[key] = skill

        # Warn if a core skill has no help group — every command must be
        # discoverable via /help.  See CLAUDE.md "User manual maintenance".
        if skill.scope == "core" and not skill.group:
            _log.warning(
                "Core skill %s has no 'group:' in SKILL.md — "
                "it won't appear in /help. Add a group field.",
                key,
            )

        # Map each valid command name and alias to this skill
        for cmd in valid_commands:
            self._check_collision(cmd.name, skill, is_alias=False)
            self._command_map[cmd.name] = skill
            for alias in cmd.aliases:
                self._check_collision(alias, skill, is_alias=True)
                self._command_map[alias] = skill

    def _check_collision(self, name: str, skill: Skill, *, is_alias: bool) -> None:
        """Log a warning if *name* is already registered by a different skill."""
        existing = self._command_map.get(name)
        if existing is not None and existing.qualified_name != skill.qualified_name:
            kind = "alias" if is_alias else "command"
            _log.warning(
                "Skill %s: %s '%s' collides with skill %s — "
                "the earlier registration will be overwritten.",
                skill.qualified_name, kind, name, existing.qualified_name,
            )

    def get(self, scope: str, name: str) -> Optional[Skill]:
        return self._skills.get(f"{scope}.{name}")

    def get_by_qualified_name(self, qualified: str) -> Optional[Skill]:
        return self._skills.get(qualified)

    def find_by_command(self, command_name: str) -> Optional[Skill]:
        """Find a skill that handles the given command name."""
        return self._command_map.get(command_name)

    def suggest_command(self, command_name: str, extra_commands: Optional[List[str]] = None) -> Optional[str]:
        """Suggest the closest matching command name for a typo.

        Args:
            command_name: The mistyped command name (without /).
            extra_commands: Additional command names to consider (e.g. hardcoded core commands).

        Returns:
            The closest command name, or None if no close match found.
        """
        import difflib

        candidates = list(self._command_map.keys())
        if extra_commands:
            candidates.extend(extra_commands)

        matches = difflib.get_close_matches(command_name, candidates, n=1, cutoff=0.5)
        return matches[0] if matches else None

    def list_all(self) -> List[Skill]:
        return list(self._skills.values())

    def list_by_scope(self, scope: str) -> List[Skill]:
        return [s for s in self._skills.values() if s.scope == scope]

    def list_by_audience(self, *audiences: str) -> List[Skill]:
        """Return skills matching any of the given audience types."""
        return [s for s in self._skills.values() if s.audience in audiences]

    def list_by_group(self, group: str) -> List[Skill]:
        """Return core skills belonging to the given help group."""
        return [s for s in self._skills.values()
                if s.scope == "core" and s.group == group]

    def list_by_group_any_scope(self, group: str) -> List[Skill]:
        """Return all skills in the given group, regardless of scope.

        Used for the ``integrations`` help group, which is deliberately
        reserved for non-core skills (e.g. skills under
        ``instance/skills/<scope>/``).
        """
        return [s for s in self._skills.values() if s.group == group]

    def groups(self) -> List[str]:
        """Return sorted list of distinct help groups from core skills."""
        return sorted(set(
            s.group for s in self._skills.values()
            if s.scope == "core" and s.group
        ))

    def scopes(self) -> List[str]:
        return sorted(set(s.scope for s in self._skills.values()))

    def __len__(self) -> int:
        return len(self._skills)

    def resolve_scoped_command(self, text: str) -> Optional[Tuple["Skill", str, str]]:
        """Resolve a scoped command like 'anantys.review' or 'core.status.ping'.

        Tries two lookup strategies:
        1. By skill name: scope.skill_name (e.g., wp.refactor → skill "wp.refactor")
        2. By command name: scope.command_name (e.g., wp.wp-refactor → skill in
           scope "wp" that has a command named "wp-refactor")

        Returns:
            (skill, command_name, args) tuple, or None if no match.
        """
        parts = text.split(None, 1)
        ref = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        segments = ref.split(".")

        if len(segments) < 2:
            return None

        scope = segments[0]
        skill_name = segments[1]
        subcommand = segments[2] if len(segments) > 2 else skill_name

        # Strategy 1: look up by skill name (scope.skill_name)
        skill = self.get(scope, skill_name)
        if skill is not None:
            return skill, subcommand, args

        # Strategy 2: look up by command name or alias within the scope
        # This handles the case where /skill listing shows /{scope}.{cmd.name}
        # but the command name differs from the skill name.
        for s in self.list_by_scope(scope):
            for cmd in s.commands:
                if cmd.name == skill_name or skill_name in cmd.aliases:
                    return s, skill_name, args

        return None

    def __contains__(self, qualified_name: str) -> bool:
        return qualified_name in self._skills


# ---------------------------------------------------------------------------
# Skill execution
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    """Context passed to skill handlers."""

    koan_root: Path
    instance_dir: Path
    command_name: str = ""
    args: str = ""
    send_message: Optional[Callable[[str], Any]] = None
    handle_chat: Optional[Callable[[str], Any]] = None


def execute_skill(skill: Skill, ctx: SkillContext) -> Optional[Union[str, SkillError]]:
    """Execute a skill and return the response text.

    Handler-based skills: imports handler.py and calls handle(ctx).
    Prompt-based skills: returns the prompt body (caller sends to Claude).

    Returns:
        Response text, SkillError on handler crash, or None if no handler.
    """
    if skill.has_handler():
        return _execute_handler(skill, ctx)
    if skill.prompt_body:
        return _execute_prompt(skill, ctx)
    return None


# Track which skills have already had their requirements satisfied this session
_requirements_satisfied: Set[str] = set()


def ensure_requirements(skill: Skill) -> Optional[str]:
    """Check and install missing Python packages declared in a skill's requirements.

    Returns None on success, or an error message string on failure.
    """
    if not skill.requirements:
        return None

    # Skip if already checked this session
    if skill.qualified_name in _requirements_satisfied:
        return None

    missing = []
    for pkg in skill.requirements:
        # Normalize: pip package names use hyphens, but import names use underscores
        import_name = pkg.replace("-", "_").split(">=")[0].split("==")[0].split("<")[0].strip()
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pkg)

    if not missing:
        _requirements_satisfied.add(skill.qualified_name)
        return None

    # Install missing packages
    _log.info(
        "[skills] auto-installing %s for skill %s",
        ", ".join(missing), skill.qualified_name,
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            error_msg = (
                f"Failed to install requirements for skill {skill.qualified_name}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
            _log.error(error_msg)
            return error_msg

        _requirements_satisfied.add(skill.qualified_name)
        return None
    except subprocess.TimeoutExpired:
        error_msg = f"Timeout installing requirements for skill {skill.qualified_name}"
        _log.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"Error installing requirements for skill {skill.qualified_name}: {e}"
        _log.error(error_msg)
        return error_msg


_MODULES_TO_REFRESH = (
    "app.github_skill_helpers",
    "app.github_url_parser",
    "app.missions",
)


def _refresh_stale_app_modules() -> None:
    """Reload app.* modules cached in sys.modules before handler execution.

    Skill handlers are loaded fresh via exec_module() each invocation, but
    their ``import app.foo`` statements resolve from sys.modules.  After an
    auto-update the cached entry may be stale (missing new functions/args),
    causing TypeErrors at call sites.  Reloading here fixes all handlers at
    once — current and future — without per-handler boilerplate.

    If reload fails (e.g. partial write during update), the stale entry is
    evicted so the handler's own ``import`` fetches a fresh copy from disk.
    """
    for name in _MODULES_TO_REFRESH:
        mod = sys.modules.get(name)
        if mod is not None:
            try:
                importlib.reload(mod)
            except Exception as e:
                _log.debug("Failed to reload %s, evicting: %s", name, e)
                sys.modules.pop(name, None)


def _execute_handler(skill: Skill, ctx: SkillContext) -> Optional[Union[str, SkillError]]:
    """Load and execute a Python handler."""
    handler_path = skill.handler_path
    if handler_path is None:
        return None

    # Auto-install declared requirements before first execution
    req_error = ensure_requirements(skill)
    if req_error:
        return SkillError(
            skill_name=skill.qualified_name,
            exception=RuntimeError(req_error),
            message=req_error,
        )

    try:
        _refresh_stale_app_modules()

        spec = importlib.util.spec_from_file_location(
            f"skill_handler_{skill.qualified_name}",
            str(handler_path),
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handle_fn = getattr(module, "handle", None)
        if handle_fn is None:
            return None

        return handle_fn(ctx)
    except Exception as e:
        _log.error("Skill handler %s failed: %s", skill.qualified_name, e, exc_info=True)
        return SkillError(
            skill_name=skill.qualified_name,
            exception=e,
            message=f"Skill error ({skill.qualified_name}): {e}",
        )


def _execute_prompt(skill: Skill, ctx: SkillContext) -> Optional[str]:
    """Return the prompt body for Claude-based execution.

    The caller is responsible for sending this to Claude.
    """
    return skill.prompt_body


# ---------------------------------------------------------------------------
# Default skills directory
# ---------------------------------------------------------------------------

def get_default_skills_dir() -> Path:
    """Return the default skills directory (koan/skills/)."""
    return Path(__file__).parent.parent / "skills"


def build_registry(extra_dirs: Optional[List[Path]] = None) -> SkillRegistry:
    """Build a registry from the default skills dir + optional extra dirs.

    Args:
        extra_dirs: Additional directories to scan (e.g., instance/skills/).
    """
    registry = SkillRegistry(get_default_skills_dir())

    if extra_dirs:
        for d in extra_dirs:
            if d.is_dir():
                for skill_md in sorted(d.rglob("SKILL.md")):
                    skill = parse_skill_md(skill_md)
                    if skill:
                        registry._register(skill)

    return registry
