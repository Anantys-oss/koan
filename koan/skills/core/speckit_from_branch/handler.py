"""Kōan speckit_from_branch skill -- resume spec-kit from a human-validated spec.

The spec on ``<branch-name>`` was authored and validated by a human and pushed,
so Kōan skips ``specify`` and runs ``plan -> tasks -> implement -> review -> CI
-> PR`` against it. This handler runs the CODE-ENFORCED pre-gates (constitution)
and queues a single mission (FR-018); the pipeline runs later in the agent loop.

Note: the dedicated ``speckit_from_branch_runner`` (specify-skip + branch-off
git flow) is the remaining US5 piece — see
``specs/001-speckit-native-support/tasks.md`` (T023/T025).
"""

from app import speckit_orchestration as orch


def handle(ctx):
    """Handle ``/speckit_from_branch <repo-id> <branch-name>``.

    Gates on the target project's constitution (FR-003), then queues a single
    mission tagged with the resolved project. Returns a reply string.
    """
    args = (ctx.args or "").strip()
    if not args:
        return "📖 Usage: /speckit_from_branch <repo-id> <branch-name>"

    parts = args.split()
    repo_id = parts[0]
    branch_name = parts[1] if len(parts) > 1 else ""
    if not branch_name:
        return "📖 Usage: /speckit_from_branch <repo-id> <branch-name>"

    project_path, project_name = orch.resolve_target(repo_id)
    if not project_path:
        return f"⚠️ Unknown project/repo: {repo_id}"

    if not orch.has_constitution(project_path):
        return (
            f"❌ speckit abort: `{project_name}` has no constitution at "
            "`.specify/memory/constitution.md`. Add one (or run spec-kit init) "
            "before using /speckit_from_branch."
        )

    queued = orch.queue_mission(
        ctx.instance_dir, "speckit_from_branch", project_name, f"{repo_id} {branch_name}",
    )
    if not queued:
        return f"ℹ️ A /speckit_from_branch mission for {project_name} is already queued."
    return f"🌿 Queued /speckit_from_branch for {project_name} (branch: {branch_name})"
