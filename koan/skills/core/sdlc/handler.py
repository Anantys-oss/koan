"""SDLC skill handler stub.

Full implementation in #1707 (orchestrator with phase routing).
This stub ensures the skill registry and tests pass while the feature is
developed across multiple PRs (#1704 state layer, #1705 prompts, #1707 orchestrator).
"""

from app.skills import SkillContext


def handle(ctx: SkillContext) -> str:
    return (
        "🚧 /sdlc is coming soon — the prompt corpus is ready (#1705) "
        "and the orchestrator is tracked in #1707."
    )
