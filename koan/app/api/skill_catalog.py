"""Public, fail-closed skill catalogue shared by REST and MCP."""

import logging
from collections.abc import Iterable

from app.skills import (
    Skill,
    SkillRegistry,
    build_registry,
    get_core_skills_dir,
    parse_skill_md,
)

_log = logging.getLogger(__name__)


def exposed_core_skills(
    registry: SkillRegistry | None = None,
) -> tuple[Skill, ...]:
    """Return API-exposed core skills in deterministic order."""
    resolved = registry or build_registry()
    exposed = tuple(
        sorted(
            (
                skill
                for skill in resolved.list_by_scope("core")
                if skill.api_exposed
            ),
            key=lambda skill: skill.name,
        )
    )
    # Only the real (default) build reconciles against the core directory;
    # test registries pass their own scoped dir and must not trigger the check.
    if registry is None:
        _warn_missing_exposed(exposed)
    return exposed


def _warn_missing_exposed(loaded: tuple[Skill, ...]) -> None:
    """Log when a core skill declaring ``api_exposed`` failed to load.

    ``parse_skill_md`` silently drops unreadable/unparseable SKILL.md files,
    so a marked skill could vanish from the catalogue with no signal. Surfacing
    the mismatch here keeps the fail-closed guarantee from hiding a partial
    catalogue behind a silent drop.
    """
    core_dir = get_core_skills_dir()
    if not core_dir.is_dir():
        return
    loaded_names = {skill.name for skill in loaded}
    for skill_md in sorted(core_dir.rglob("SKILL.md")):
        parsed = parse_skill_md(skill_md)
        if parsed is not None and parsed.api_exposed and parsed.name not in loaded_names:
            _log.error(
                "API-exposed core skill '%s' (%s) was not loaded into the "
                "catalogue; the exposed surface may be partial",
                parsed.name,
                skill_md,
            )


def build_skill_catalog(skills: Iterable[Skill]) -> list[dict[str, object]]:
    """Serialize exposed skills without loading private instance scopes."""
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "group": skill.group,
            "emoji": skill.emoji,
            "commands": [
                {
                    "name": f"/{command.name}",
                    "description": command.description,
                    "usage": command.usage or f"/{command.name}",
                    "aliases": [f"/{alias}" for alias in command.aliases],
                }
                for command in sorted(
                    skill.commands,
                    key=lambda command: command.name,
                )
            ],
        }
        for skill in skills
    ]


def _accepted_command_names(
    skills: Iterable[Skill],
) -> tuple[str, ...]:
    """Return every canonical command name plus its aliases, slash-prefixed.

    This is the *advertised* surface — the OpenAPI ``command`` enum and the MCP
    tool schema built from it. Aliases belong in it because the catalogue
    publishes them as first-class inputs; ``routes_missions`` normalizes them to
    the canonical command name before queueing. It is not an accept-list: the
    mission endpoint still queues any slash command (see ``_normalize_command``).
    """
    names = set()
    for skill in skills:
        for command in skill.commands:
            names.add(f"/{command.name}")
            names.update(f"/{alias}" for alias in command.aliases)
    return tuple(sorted(names))


EXPOSED_CORE_SKILLS = exposed_core_skills()
SKILL_CATALOG = build_skill_catalog(EXPOSED_CORE_SKILLS)
# An empty catalogue leaves mission creation working (the endpoint does not
# gate on this set) but strips REST and MCP of every advertised command, so a
# client has nothing to discover. Surface it rather than degrade silently.
if not EXPOSED_CORE_SKILLS:
    _log.error(
        "Skill catalogue is empty: no API-exposed core skills found. "
        "REST and MCP clients will advertise no slash commands.",
    )
API_COMMAND_NAMES = _accepted_command_names(EXPOSED_CORE_SKILLS)
_ALIAS_TO_CANONICAL = {
    alias: command.name
    for skill in EXPOSED_CORE_SKILLS
    for command in skill.commands
    for alias in command.aliases
}


def canonical_command_name(verb: str) -> str:
    """Resolve one advertised verb to its canonical command name.

    An unknown verb is returned unchanged; the caller decides whether that is
    a rejection, so the alias table never has to double as a membership test.
    """
    return _ALIAS_TO_CANONICAL.get(verb, verb)
