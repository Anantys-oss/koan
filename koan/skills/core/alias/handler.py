"""Kōan alias skill — create short aliases for project names."""

import json
from pathlib import Path

from app.utils import is_known_project


ALIASES_FILE = ".project-aliases.json"


def _aliases_path(ctx) -> Path:
    return ctx.instance_dir / ALIASES_FILE


def _load_aliases(ctx) -> dict:
    path = _aliases_path(ctx)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_aliases(ctx, aliases: dict):
    from app.utils import atomic_write
    atomic_write(_aliases_path(ctx), json.dumps(aliases, indent=2) + "\n")


def handle(ctx):
    if ctx.command_name == "unalias":
        return _handle_unalias(ctx)
    return _handle_alias(ctx)


def _handle_alias(ctx):
    args = ctx.args.strip() if ctx.args else ""
    if not args:
        return _list_aliases(ctx)

    parts = args.split()

    if parts[0] == "--rm":
        if len(parts) < 2:
            return "Usage: /alias --rm <shortcut>"
        ctx.args = parts[1]
        return _handle_unalias(ctx)

    if len(parts) < 2:
        return "Usage: /alias <project> <shortcut>\nExample: /alias Template2 tt"
    if len(parts) > 2:
        return "Too many arguments. Usage: /alias <project> <shortcut>"

    project_name, shortcut = parts[0], parts[1].lower()

    if not is_known_project(project_name):
        return f"❌ Unknown project: {project_name}\nUse /projects to see available projects."

    from app.bridge_state import _get_registry
    registry = _get_registry()
    if registry.find_by_command(shortcut):
        return f"❌ '{shortcut}' conflicts with existing command /{shortcut}"

    from app.command_handlers import CORE_COMMANDS
    if shortcut in CORE_COMMANDS:
        return f"❌ '{shortcut}' conflicts with core command /{shortcut}"

    aliases = _load_aliases(ctx)
    aliases[shortcut] = project_name
    _save_aliases(ctx, aliases)

    return f"🔗 Alias created: /{shortcut} → {project_name}\nUse: /{shortcut} <mission text>"


def _handle_unalias(ctx):
    shortcut = ctx.args.strip().lower() if ctx.args else ""
    if not shortcut:
        return "Usage: /unalias <shortcut>"

    aliases = _load_aliases(ctx)
    if shortcut not in aliases:
        return f"❌ No alias '{shortcut}' found."

    project = aliases.pop(shortcut)
    _save_aliases(ctx, aliases)
    return f"🔗 Alias removed: /{shortcut} (was → {project})"


def _list_aliases(ctx):
    aliases = _load_aliases(ctx)
    if not aliases:
        return "No project aliases defined.\nCreate one: /alias <project> <shortcut>"

    lines = ["Project aliases:"]
    for shortcut, project in sorted(aliases.items()):
        lines.append(f"  /{shortcut} → {project}")
    lines.append("\nRemove with: /alias --rm <shortcut>")
    return "\n".join(lines)
