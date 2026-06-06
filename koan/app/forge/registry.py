"""Forge provider registry — maps type strings to ForgeProvider classes.

Phase 1: GitHub only.
Phase 2:  Gogs (self-hosted) added.
Phase 3a: GitLabForge will be added here.
Phase 3b: GiteaForge will be added here.
"""

from typing import Type

from app.forge.base import ForgeProvider
from app.forge.github import GitHubForge
from app.forge.gogs import GogsForge


# Map forge type strings to provider classes.
# Keys are the values accepted in projects.yaml under `forge:`.
FORGE_TYPES: dict = {
    "github": GitHubForge,
    "gogs": GogsForge,
    # "gitlab": GitLabForge,   # Phase 3a
    # "gitea": GiteaForge,     # Phase 3b
}

DEFAULT_FORGE = "github"


def get_forge_class(forge_type: str) -> Type[ForgeProvider]:
    """Return the ForgeProvider class for the given forge type string.

    Args:
        forge_type: Forge identifier (e.g. "github", "gitlab", "gitea").

    Returns:
        The corresponding ForgeProvider subclass.

    Raises:
        ValueError: If forge_type is not a recognised forge identifier.
    """
    cls = FORGE_TYPES.get(forge_type)
    if cls is None:
        supported = ", ".join(sorted(FORGE_TYPES))
        raise ValueError(
            f"Unknown forge type: {forge_type!r}. "
            f"Supported types: {supported}"
        )
    return cls
