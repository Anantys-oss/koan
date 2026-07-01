"""Kōan speckit skill -- bridge handler for the native /speckit command.

The handler runs the CODE-ENFORCED pre-gates (constitution presence) and queues
a single /speckit mission. The pipeline itself runs later in the agent loop via
``speckit_runner`` (auto-discovered by skill_dispatch).
"""

from app import speckit_orchestration as orch


def handle(ctx):
    """Handle ``/speckit <project> <goal>`` (or ``/speckit <issue-url>``).

    Gates on the target project's constitution (FR-003), then queues a single
    mission (FR-018). Returns a reply string for the operator.
    """
    args = (ctx.args or "").strip()
    if not args:
        return (
            "📖 Usage: /speckit <project> <goal>  |  "
            "/speckit <issue-url> [repo:.. branch:..]"
        )

    parts = args.split(None, 1)
    project_arg = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # Issue-URL trigger: the URL is the goal (the runner/agent fetches the issue
    # content). Chat trigger: the rest is the goal. `repo:`/`branch:` override
    # tokens are forwarded verbatim; the runner parses them (FR-007).
    if project_arg.lower().startswith(("http://", "https://")):
        goal = (project_arg + " " + rest).strip()
    else:
        goal = rest.strip()

    project_path, project_name = orch.resolve_target(project_arg)
    if not project_path:
        return f"⚠️ Unknown project: {project_arg}"

    if not orch.has_constitution(project_path):
        return (
            f"❌ speckit abort: `{project_name}` has no constitution at "
            "`.specify/memory/constitution.md`. Add one (or run spec-kit init) "
            "before using /speckit."
        )

    if not goal:
        return "📖 Usage: /speckit <project> <goal>"

    queued = orch.queue_mission(ctx.instance_dir, "speckit", project_name, goal)
    if not queued:
        return f"ℹ️ A /speckit mission for {project_name} is already queued."
    return f"📋 Queued /speckit for {project_name}: {goal}"
