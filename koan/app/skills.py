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
import os
import re
import subprocess
import sys
import time
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
    # ``caveman_enabled`` follows the SKILL.md frontmatter ``caveman:`` flag.
    # Default ``False`` (opt-in): a skill must declare ``caveman: true`` in
    # its frontmatter (or be listed in ``optimizations.caveman.include`` in
    # ``config.yaml``) for the caveman directive to be appended.  Skills
    # are also free to keep an explicit ``caveman: false`` to document
    # intent, even though it matches the default.
    caveman_enabled: bool = False
    # ``forward_result_enabled`` follows the SKILL.md frontmatter
    # ``forward_result:`` flag. When True, the post-mission pipeline forwards
    # the Claude session's result text to outbox.md so the user sees the
    # response to their slash command / @mention. Auto-derived markers
    # (slash-command forms of every command + alias, plus ``/{scope}.{name}``)
    # are matched against the mission title in addition to any explicit
    # ``title_markers``.
    forward_result_enabled: bool = False
    # ``title_markers`` — optional list of additional mission-title substrings
    # that should also flag a mission as belonging to this skill, for the case
    # where a handler emits plain-text titles without the slash command.
    title_markers: List[str] = field(default_factory=list)
    # ``sub_commands`` — optional list of skill commands to queue when this
    # skill is triggered.  Combo skills (e.g. /rr → /review + /rebase) declare
    # their expansion in SKILL.md frontmatter rather than in a hardcoded dict.
    sub_commands: List[str] = field(default_factory=list)
    parallel_sub_commands: bool = False
    requirements: List[str] = field(default_factory=list)
    # ``model_key`` — optional key used to resolve the model name shown in PR
    # footers (e.g. "mission"). When set, the agent loop forwards it to the
    # skill subprocess via ``KOAN_MISSION_MODEL_KEY``.
    model_key: str = ""
    iterative: bool = False
    # ``chat_confirmable`` follows the SKILL.md frontmatter ``chat_confirmable:``
    # flag. Default ``False`` (opt-in): a skill must declare
    # ``chat_confirmable: true`` for the chat bridge to offer one-word ("yes")
    # confirmation that runs its slash command. Execution still flows through
    # the normal ``handle_command`` path with every existing gate — this flag
    # only authorizes the *offer*. Destructive commands must stay opt-out.
    chat_confirmable: bool = False

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
            # Block list of command dicts (or simple strings)
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
                elif cline.startswith("- ") and ":" not in cline:
                    # Simple string entry: "- models"
                    if current_cmd:
                        commands.append(current_cmd)
                    current_cmd = {"name": cline[2:].strip()}
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
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
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


# All frontmatter keys recognized by parse_skill_md(). Used to flag typos
# (e.g. ``descrption:``) as unknown keys at parse time rather than silently
# dropping them. ``aliases``/``usage`` are command-level keys (nested under
# ``commands:``), not top-level, so they are excluded here.
_KNOWN_SKILL_KEYS = frozenset({
    "name", "scope", "description", "version", "commands", "handler",
    "worker", "github_enabled", "github_context_aware", "caveman",
    "forward_result", "iterative", "title_markers", "audience", "cli_skill",
    "group", "emoji", "requirements", "sub_commands", "parallel", "model_key",
    "chat_confirmable",
})


def validate_skill_metadata(meta: Dict[str, Any], path: Path) -> List[str]:
    """Return human-readable warnings about SKILL.md frontmatter problems.

    Catches the silent-failure modes called out in the skill authoring guide:
    typo'd keys (``descrption:``), missing required fields, and a declared
    ``handler:`` whose file is absent. Returns an empty list when the metadata
    is clean. Pure function — never logs or raises — so callers decide how to
    surface the warnings (logged at registry build, asserted in tests).
    """
    import difflib

    warnings: List[str] = []

    # Required non-empty fields. ``name`` is enforced separately by the caller
    # (a missing name means the file isn't a parseable skill at all).
    if not str(meta.get("description", "")).strip():
        warnings.append("missing required field 'description'")

    commands = meta.get("commands")
    if not commands:
        warnings.append("missing required field 'commands'")
    elif isinstance(commands, list) and not any(
        isinstance(c, dict) and c.get("name") for c in commands
    ):
        # Inline form (``commands: [a, b]``) parses to bare strings, which
        # parse_skill_md() silently drops — the skill ends up uninvokable.
        warnings.append(
            "'commands' has no valid entries — use block form with "
            "'- name: <cmd>' so the command is registered"
        )

    # Unknown keys — almost always typos. Suggest the nearest known key.
    for key in meta:
        if key not in _KNOWN_SKILL_KEYS:
            suggestion = difflib.get_close_matches(key, _KNOWN_SKILL_KEYS, n=1, cutoff=0.6)
            hint = f" (did you mean '{suggestion[0]}'?)" if suggestion else ""
            warnings.append(f"unknown key '{key}'{hint}")

    # Cross-reference: a declared handler must exist on disk.
    handler_name = str(meta.get("handler", "")).strip()
    if handler_name and not (path.parent / handler_name).exists():
        warnings.append(f"declared handler '{handler_name}' not found in skill directory")

    return warnings


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

    # Surface frontmatter problems (typos, missing fields, dangling handler)
    # at parse time so they show up in startup logs instead of failing silently.
    for warning in validate_skill_metadata(meta, path):
        _log.warning("Skill %s: %s", path.parent.name, warning)

    # Parse commands
    commands = [
        SkillCommand(
            name=cmd_data["name"],
            description=cmd_data.get("description", ""),
            aliases=cmd_data.get("aliases", []),
            usage=cmd_data.get("usage", ""),
        )
        for cmd_data in meta.get("commands", [])
        if isinstance(cmd_data, dict) and "name" in cmd_data
    ]

    # Resolve handler path (always record declared path; has_handler() checks existence)
    handler_path = None
    handler_name = meta.get("handler", "")
    if handler_name:
        handler_path = path.parent / handler_name

    skill_dir = path.parent

    # Parse boolean flags — caveman is opt-in (defaults to False).
    worker = _parse_bool_flag(meta, "worker")
    github_enabled = _parse_bool_flag(meta, "github_enabled")
    github_context_aware = _parse_bool_flag(meta, "github_context_aware")
    caveman_enabled = _parse_bool_flag(meta, "caveman")
    forward_result_enabled = _parse_bool_flag(meta, "forward_result")
    iterative = _parse_bool_flag(meta, "iterative")
    chat_confirmable = _parse_bool_flag(meta, "chat_confirmable")

    # Parse title_markers (optional inline list or comma-separated scalar).
    title_markers_raw = meta.get("title_markers", [])
    if isinstance(title_markers_raw, list):
        title_markers = [str(m).strip() for m in title_markers_raw if str(m).strip()]
    elif isinstance(title_markers_raw, str) and title_markers_raw.strip():
        title_markers = [s.strip() for s in title_markers_raw.split(",") if s.strip()]
    else:
        title_markers = []

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

    # Parse sub_commands (for combo skill expansion)
    sub_commands_raw = meta.get("sub_commands", [])
    if isinstance(sub_commands_raw, str):
        sub_commands_raw = [sub_commands_raw] if sub_commands_raw else []
    sub_commands = [s for s in sub_commands_raw if s]

    # Parse parallel flag (for combo skills batch insertion)
    parallel_sub_commands = _parse_bool_flag(meta, "parallel")

    # Parse model_key (for PR footer model resolution)
    model_key = str(meta.get("model_key", "") or "").strip()

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
        caveman_enabled=caveman_enabled,
        forward_result_enabled=forward_result_enabled,
        title_markers=title_markers,
        sub_commands=sub_commands,
        parallel_sub_commands=parallel_sub_commands,
        requirements=requirements,
        model_key=model_key,
        iterative=iterative,
        chat_confirmable=chat_confirmable,
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


def collect_forward_result_markers(registry: "SkillRegistry") -> List[str]:
    """Return mission-title substrings for every skill that opted into result forwarding.

    For each skill with ``forward_result_enabled``:
      - emit ``/{cmd.name}`` and ``/{alias}`` for every command + alias,
      - emit ``/{scope}.{name}`` (the scoped form used when a project tag is
        present — see ``command_handlers._queue_cli_skill_mission``),
      - emit every entry from ``title_markers`` (for handler-composed
        plain-text mission titles).

    All markers are lower-cased and deduplicated so the caller can do a flat
    case-insensitive substring check against the mission title.
    """
    markers: set[str] = set()
    for skill in registry.list_all():
        if not skill.forward_result_enabled:
            continue
        markers.add(f"/{skill.scope}.{skill.name}".lower())
        for cmd in skill.commands:
            markers.add(f"/{cmd.name}".lower())
            for alias in cmd.aliases:
                markers.add(f"/{alias}".lower())
        for raw in skill.title_markers:
            text = (raw or "").strip().lower()
            if text:
                markers.add(text)
    return sorted(markers)


ComboSkill = namedtuple("ComboSkill", ["commands", "parallel"], defaults=[False])


def collect_combo_skills(registry: "SkillRegistry") -> Dict[str, "ComboSkill"]:
    """Build a mapping of command names/aliases to combo skill info.

    Iterates all skills with ``sub_commands`` defined in their SKILL.md
    frontmatter and maps every command name + alias to the expansion info.

    Returns:
        Dict mapping command name or alias to ComboSkill(commands, parallel).
        Example: {"rr": ComboSkill(["review", "rebase"], False)}
    """
    mapping: Dict[str, ComboSkill] = {}
    for skill in registry.list_all():
        if not skill.sub_commands:
            continue
        combo = ComboSkill(commands=skill.sub_commands, parallel=skill.parallel_sub_commands)
        for cmd in skill.commands:
            mapping[cmd.name] = combo
            for alias in cmd.aliases:
                mapping[alias] = combo
    return mapping


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
    project_name: str = ""
    _memory: Any = field(init=False, default=None, repr=False)

    @property
    def memory(self):
        """Lazy :class:`~app.skill_memory_accessor.MemoryAccessor` instance.

        Constructed on first access so skills that never touch memory pay
        nothing. Read methods take ``project`` as a parameter; pass
        ``ctx.project_name`` when no explicit project is in scope.
        """
        if self._memory is None:
            from app.skill_memory_accessor import MemoryAccessor
            self._memory = MemoryAccessor(self.instance_dir)
        return self._memory


def execute_skill(skill: Skill, ctx: SkillContext) -> Optional[Union[str, SkillError]]:
    """Execute a skill and return the response text.

    Handler-based skills: imports handler.py and calls handle(ctx).
    Prompt-based skills: returns the prompt body (caller sends to Claude).
    Combo skills (sub_commands, no handler): expands into pending missions.

    Returns:
        Response text, SkillError on handler crash, or None if no handler.
    """
    if skill.has_handler():
        return _execute_handler(skill, ctx)
    if skill.prompt_body:
        return _execute_prompt(skill, ctx)
    if skill.sub_commands:
        return _execute_combo_skill(skill, ctx)
    return None


def _execute_combo_skill(skill: Skill, ctx: SkillContext) -> str:
    """Expand a combo skill into pending missions via expand_combo_skill()."""
    from app.skill_dispatch import expand_combo_skill
    from app.utils import get_known_projects, resolve_project_alias

    args = ctx.args.strip()
    project = None
    mission_args = args

    words = args.split(None, 1)
    if words:
        known_map = {name.lower(): name for name, _ in get_known_projects()}
        matched = known_map.get(words[0].lower())
        if not matched:
            matched = resolve_project_alias(words[0])
        if matched:
            project = matched
            mission_args = words[1] if len(words) > 1 else ""

    tag = f"[project:{project}] " if project else ""
    mission_text = f"{tag}/{ctx.command_name} {mission_args}".rstrip()

    expanded = expand_combo_skill(mission_text, str(ctx.instance_dir))
    if not expanded:
        return f"Failed to expand combo /{ctx.command_name} — skill not found in dispatch cache"

    sub_list = ", ".join(f"/{c}" for c in skill.sub_commands)
    ack = f"Combo /{ctx.command_name} expanded into: {sub_list}"
    if project:
        ack += f" (project: {project})"
    return ack


# Captured at import time so first-time observations in
# _refresh_stale_app_modules can tell whether a module's source file has been
# rewritten by auto-update since this process started (Python had no chance to
# pick up the new content because sys.modules still holds the pre-update copy).
_PROCESS_START_TIME: float = time.time()
# mtime cache: module_name -> last-seen mtime (float)
_module_mtimes: Dict[str, float] = {}

# Track which skills have already had their requirements satisfied this session
_requirements_satisfied: Set[str] = set()


def _reset_requirements_cache() -> None:
    """Clear the per-session requirements cache (used by tests)."""
    _requirements_satisfied.clear()


def ensure_requirements(skill: Skill) -> Optional[str]:
    """Check and install missing Python packages declared in a skill's requirements.

    Returns None on success, or an error message string on failure.
    """
    reqs = getattr(skill, "requirements", [])
    if not reqs:
        return None

    # Skip if already checked this session
    if skill.qualified_name in _requirements_satisfied:
        return None

    # Reject entries that look like pip CLI flags (e.g. --index-url)
    for pkg in reqs:
        if pkg.startswith("-"):
            return f"Invalid requirement '{pkg}' for skill {skill.qualified_name}: flags not allowed"

    missing = []
    for pkg in reqs:
        # Normalize: pip package names use hyphens, but import names use underscores
        # Split on any PEP 440 version operator (~=, >=, <=, !=, ===, ==, >, <)
        import_name = re.split(r'[><=!~]', pkg)[0].replace("-", "_").strip()
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


def _refresh_stale_app_modules() -> None:
    """Reload app.* modules whose source files changed on disk.

    Skill handlers are loaded fresh via exec_module() each invocation, but
    their ``import app.foo`` statements resolve from sys.modules.  After an
    auto-update the cached entry may be stale (missing new functions/args),
    causing TypeErrors at call sites.

    Instead of maintaining a hardcoded list of modules to refresh, this
    checks the mtime of every loaded ``app.*`` module's source file.  Only
    modules whose file actually changed are reloaded — typically zero on
    most invocations, making this cheap in the common case.

    If reload fails (e.g. partial write during update), the stale entry is
    evicted so the handler's own ``import`` fetches a fresh copy from disk.
    """
    for name in list(sys.modules):
        if not name.startswith("app."):
            continue
        mod = sys.modules.get(name)
        if mod is None:
            continue
        source = getattr(mod, "__file__", None)
        if source is None:
            continue
        try:
            current_mtime = os.path.getmtime(source)
        except OSError:
            continue
        cached_mtime = _module_mtimes.get(name)
        if cached_mtime is not None and current_mtime == cached_mtime:
            continue
        # Reload when either: (a) we have a baseline and the file changed, or
        # (b) this is the first observation but the file was modified after the
        # process started — i.e. auto-update rewrote it before we built a baseline.
        should_reload = (
            cached_mtime is not None
            or current_mtime > _PROCESS_START_TIME
        )
        if should_reload:
            try:
                importlib.reload(mod)
                _log.debug("Reloaded stale module %s", name)
            except Exception as e:
                _log.debug("Failed to reload %s, evicting: %s", name, e)
                sys.modules.pop(name, None)
                _module_mtimes.pop(name, None)
                continue
        _module_mtimes[name] = current_mtime


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
            exception=str(RuntimeError(req_error)),
            message=req_error,
        )

    try:
        _refresh_stale_app_modules()

        # Ensure the parent of the skills/ package directory resolves BEFORE
        # every other sys.path entry so handler imports like
        # ``from skills.core.X import Y`` resolve to the koan/skills/ *package*.
        # A ``python app/run.py`` launch puts koan/app/ at sys.path[0], and that
        # directory contains app/skills.py — a module that shadows the package
        # and makes such imports fail with "'skills' is not a package".  Merely
        # appending koan/ (it is usually already present via PYTHONPATH=.) is not
        # enough; it must come first.
        _skills_pkg_parent = str(get_default_skills_dir().resolve().parent)
        if not sys.path or sys.path[0] != _skills_pkg_parent:
            while _skills_pkg_parent in sys.path:
                sys.path.remove(_skills_pkg_parent)
            sys.path.insert(0, _skills_pkg_parent)

        # If a prior import already resolved bare ``skills`` to app/skills.py (a
        # module, not the package), evict it so the corrected sys.path order
        # re-imports the real koan/skills/ package on the handler's first import.
        _cached_skills = sys.modules.get("skills")
        if _cached_skills is not None and not hasattr(_cached_skills, "__path__"):
            sys.modules.pop("skills", None)

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
        # Store exception as string — raw exception objects are not JSON-
        # serializable and leak into requests.post(json=...) if the SkillError
        # bypasses isinstance() checks after a module reload.
        return SkillError(
            skill_name=skill.qualified_name,
            exception=f"{type(e).__name__}: {e}",
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


def get_core_skills_dir() -> Path:
    """Return the core skills directory (koan/skills/core/).

    Core discovery is scoped to this subdirectory: ``koan/skills/`` is reserved
    for core skills, and custom scopes belong under ``instance/skills/<scope>/``.
    Scanning only ``core/`` keeps a stray custom scope dropped under the core
    tree out of the core registry. See CLAUDE.md skills boundary + issue #2084.
    """
    return get_default_skills_dir() / "core"


def _warn_misplaced_core_scopes() -> None:
    """Emit one guidance warning if a non-core scope sits under koan/skills/.

    Such a scope is ignored by core discovery; without this the operator gets
    no hint why their skill is invisible (and previously got per-build
    'missing required field' spam, one line per misplaced skill). See #2084.
    """
    default_dir = get_default_skills_dir()
    try:
        strays = sorted(
            p.name for p in default_dir.iterdir()
            if p.is_dir() and p.name not in {"core", "__pycache__"}
        )
    except OSError:
        return
    if strays:
        _log.warning(
            "Ignoring non-core skill scope(s) under %s: %s — move custom "
            "skills to instance/skills/<scope>/ so they load correctly.",
            default_dir, ", ".join(strays),
        )


def build_registry(extra_dirs: Optional[List[Path]] = None) -> SkillRegistry:
    """Build a registry from the core skills dir + optional extra dirs.

    Args:
        extra_dirs: Additional directories to scan (e.g., instance/skills/).

    Core discovery is scoped to ``koan/skills/core/`` (see
    ``get_core_skills_dir``); custom scopes belong under ``extra_dirs``.

    Skills under ``extra_dirs`` are filtered through the approval gate:
    any SKILL.md whose own directory or an ancestor up to the extra dir
    carries a ``.koan-pending`` marker is silently skipped so the bridge
    cannot exec a handler that has not been approved.
    """
    core_dir = get_core_skills_dir()
    registry = SkillRegistry(core_dir if core_dir.is_dir() else get_default_skills_dir())
    _warn_misplaced_core_scopes()

    if extra_dirs:
        from app.skill_approval import find_pending_ancestor
        for d in extra_dirs:
            if not d.is_dir():
                continue
            for skill_md in sorted(d.rglob("SKILL.md")):
                if find_pending_ancestor(skill_md, d) is not None:
                    continue
                skill = parse_skill_md(skill_md)
                if skill:
                    registry._register(skill)

    return registry
