"""Mission parsing and project/skill discovery (no Flask)."""
from app.dashboard import state
from app.dashboard_service import read_file
from app.missions import extract_project_tag
from app.utils import get_known_projects


def parse_missions() -> dict:
    """Parse missions.md into structured sections."""
    from app.missions import parse_sections

    content = read_file(state.MISSIONS_FILE)
    if not content:
        return {"pending": [], "in_progress": [], "done": []}

    return parse_sections(content)


def filter_missions_by_project(missions: dict, project: str) -> dict:
    """Filter parsed mission sections to only items matching project tag."""
    if not project:
        return missions
    return {
        key: [m for m in items if extract_project_tag(m) == project]
        for key, items in missions.items()
    }


def get_all_project_names() -> list:
    """Return sorted list of project names from config and mission tags."""
    # Names from projects.yaml / env
    names = {name for name, _path in get_known_projects()}
    # Names from mission tags
    missions = parse_missions()
    for section in missions.values():
        for item in section:
            tag = extract_project_tag(item)
            if tag != "default":
                names.add(tag)
    return sorted(names, key=str.lower)


def get_mission_skill_commands() -> list:
    """Return sorted list of skill command names usable as missions."""
    from app.skills import build_registry

    extra_dirs = []
    instance_skills = state.INSTANCE_DIR / "skills"
    if instance_skills.is_dir():
        extra_dirs.append(instance_skills)

    registry = build_registry(extra_dirs)
    commands = set()
    for skill in registry.list_all():
        if skill.audience not in ("agent", "hybrid"):
            continue
        for cmd in skill.commands:
            commands.add(cmd.name)
    return sorted(commands, key=str.lower)
