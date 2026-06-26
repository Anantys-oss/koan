"""Agent blueprint: introspection (soul/memory/skills/config) + lifecycle controls."""
import sys
import time
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from app.dashboard import state
from app.dashboard_service import mask_sensitive

agent_bp = Blueprint("agent", __name__)


def _read_capped(path: Path, cap: int = 10_000) -> dict:
    """Read a file, capping at `cap` chars and flagging truncation."""
    if not path.exists():
        return {"content": None, "path": str(path.relative_to(state.KOAN_ROOT)), "truncated": False}
    text = path.read_text(errors="replace")
    truncated = len(text) > cap
    return {
        "content": text[:cap],
        "path": str(path.relative_to(state.KOAN_ROOT)),
        "truncated": truncated,
        "total_chars": len(text) if truncated else None,
    }


@agent_bp.route("/skills")
def skills_page():
    """Dedicated skills registry page."""
    return render_template("skills.html")


@agent_bp.route("/agent")
def agent_page():
    """Agent introspection page — soul and memory."""
    return render_template("agent.html")


@agent_bp.route("/api/agent/soul")
def api_agent_soul():
    """Return soul.md content (full, uncapped — editing needs the whole file)."""
    soul_path = state.INSTANCE_DIR / "soul.md"
    if not soul_path.exists():
        return jsonify({"content": None, "path": "instance/soul.md"})
    text = soul_path.read_text(errors="replace")
    return jsonify({"content": text, "path": "instance/soul.md"})


@agent_bp.route("/api/agent/soul", methods=["PUT"])
def api_agent_soul_save():
    """Save soul.md content atomically."""
    from app.utils import atomic_write

    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if content is None:
        return jsonify({"ok": False, "error": "Missing content"}), 400

    soul_path = state.INSTANCE_DIR / "soul.md"
    atomic_write(soul_path, content)
    return jsonify({"ok": True})


@agent_bp.route("/api/agent/memory")
def api_agent_memory():
    """Return a structured tree of memory files."""
    memory_dir = state.INSTANCE_DIR / "memory"

    if not memory_dir.exists():
        return jsonify({"summary": None, "global": [], "projects": {}})

    summary = _read_capped(memory_dir / "summary.md")

    # Global context files under memory/global/
    global_files = []
    global_dir = memory_dir / "global"
    if global_dir.is_dir():
        global_files.extend(
            {**_read_capped(f), "name": f.name}
            for f in sorted(global_dir.iterdir())
            if f.is_file() and f.suffix in (".md", ".txt")
        )

    # Per-project files under memory/projects/{name}/
    projects: dict = {}
    projects_dir = memory_dir / "projects"
    if projects_dir.is_dir():
        for proj_dir in sorted(projects_dir.iterdir()):
            if not proj_dir.is_dir():
                continue
            files = [
                {**_read_capped(f), "name": f.name}
                for f in sorted(proj_dir.iterdir())
                if f.is_file() and f.suffix in (".md", ".txt")
            ]
            if files:
                projects[proj_dir.name] = files

    return jsonify({"summary": summary, "global": global_files, "projects": projects})


@agent_bp.route("/api/agent/skills")
def api_agent_skills():
    """Return skill registry metadata."""
    from app.skills import build_registry

    now = time.time()
    cache = state._agent_skills_cache
    if "ts" in cache and now - cache["ts"] < state._AGENT_SKILLS_CACHE_TTL:
        return jsonify(cache["data"])

    extra_dirs = []
    instance_skills = state.INSTANCE_DIR / "skills"
    if instance_skills.is_dir():
        extra_dirs.append(instance_skills)

    registry = build_registry(extra_dirs)

    skills_list = []
    for skill in registry.list_all():
        commands = [
            {
                "name": cmd.name,
                "aliases": list(cmd.aliases) if cmd.aliases else [],
                "description": cmd.description or "",
            }
            for cmd in skill.commands
        ]
        skills_list.append({
            "name": skill.name,
            "scope": skill.scope,
            "group": skill.group,
            "description": skill.description or "",
            "commands": commands,
            "audience": skill.audience,
            "worker": skill.worker,
            "github_enabled": skill.github_enabled,
        })

    data = {
        "scopes": registry.scopes(),
        "groups": registry.groups(),
        "skills": skills_list,
    }
    cache["ts"] = now
    cache["data"] = data
    return jsonify(data)


@agent_bp.route("/api/agent/config")
def api_agent_config():
    """Return config.yaml and projects.yaml contents (sensitive values masked)."""
    from app.projects_config import resolve_projects_config_path

    config_path = state.KOAN_ROOT / "instance" / "config.yaml"
    projects_path = resolve_projects_config_path(str(state.KOAN_ROOT))

    def read_yaml(path: Path):
        if not path.exists():
            return None
        return mask_sensitive(path.read_text(errors="replace"))

    return jsonify({
        "config_yaml": read_yaml(config_path),
        "projects_yaml": read_yaml(projects_path),
    })


@agent_bp.route("/api/agent/pause", methods=["POST"])
def api_agent_pause():
    """Pause the agent loop, optionally for a duration (e.g. '2h', '30m')."""
    from app.pause_manager import create_pause, parse_duration

    try:
        data = request.get_json() or {}
    except Exception as exc:
        print(f"[dashboard] api_agent_pause: invalid JSON: {exc}", file=sys.stderr)
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400
    duration_str = (data.get("duration") or "").strip()

    timestamp = None
    display = ""
    if duration_str:
        secs = parse_duration(duration_str)
        if secs is None:
            return jsonify({"ok": False, "error": "Invalid duration format. Use '2h', '30m', '1h30m'"}), 422
        timestamp = int(time.time()) + secs
        display = f"Dashboard pause ({duration_str})"

    create_pause(str(state.KOAN_ROOT), "manual", timestamp=timestamp, display=display)
    return jsonify({"ok": True, "status": "paused", "duration": duration_str or None})


@agent_bp.route("/api/agent/resume", methods=["POST"])
def api_agent_resume():
    """Resume the agent loop."""
    from app.pause_manager import remove_pause

    remove_pause(str(state.KOAN_ROOT))
    return jsonify({"ok": True, "status": "resumed"})


@agent_bp.route("/api/agent/restart", methods=["POST"])
def api_agent_restart():
    """Signal the agent loop to restart."""
    # Route through request_restart() so both per-consumer markers are written
    # and the restart actually fires.
    from app.restart_manager import request_restart
    try:
        request_restart(str(state.KOAN_ROOT))
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "status": "restart_signaled"})
