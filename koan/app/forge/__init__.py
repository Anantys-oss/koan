"""Forge provider factory and auto-detection.

Primary entry point for the forge package.  Callers use get_forge() to
obtain a ForgeProvider for a project without caring about the concrete type.

Resolution order in get_forge(project_name):
  1. 'forge' field in projects.yaml for the project
  2. Auto-detect from 'forge_url' / 'github_url' domain
  3. Default: GitHubForge

Phase roadmap:
  Phase 1 (done): GitHub, base class, registry, factory
  Phase 2 (done): GogsForge (self-hosted Gogs instances)
  Phase 3a: GitLabForge
  Phase 3b: GiteaForge (Codeberg / Forgejo)
  Phase 4: forge_url config field + auto-detection from git remotes
"""

import logging
from typing import Optional

from app.forge.base import ForgeProvider
from app.forge.github import GitHubForge
from app.forge.registry import DEFAULT_FORGE, get_forge_class

log = logging.getLogger(__name__)


def get_forge(project_name: Optional[str] = None) -> ForgeProvider:
    """Return a ForgeProvider for the given project.

    Falls back to GitHubForge for any unconfigured or unknown project so
    that all existing callers work without change during the Phase 1→5
    migration period.

    Args:
        project_name: Project name as declared in projects.yaml.
                      Pass None to get the default forge.

    Returns:
        A ForgeProvider instance appropriate for the project.
    """
    forge_type, forge_url = _resolve_forge_config(project_name)

    try:
        cls = get_forge_class(forge_type)
    except ValueError:
        # Unknown forge type — fall back to GitHub to avoid breaking callers.
        cls = GitHubForge

    # If we have a forge url, pass it
    if forge_url:
        return cls(base_url=forge_url)
    return cls()


def get_forge_for_path(project_path: str) -> ForgeProvider:
    """Return a ForgeProvider for a project given only its local path.

    Convenience wrapper for callers that have a checkout path but not the
    project name.  Koan's workspace layout maps the directory basename to the
    project key in projects.yaml, so the basename is used for config lookup.
    Falls back to the default forge when the project is not configured.

    Args:
        project_path: Local path to the project repository.

    Returns:
        A ForgeProvider instance appropriate for the project.
    """
    from app.utils import find_known_project_name_for_path
    import os

    name = find_known_project_name_for_path(project_path) if project_path else None
    if name is None and project_path:
        name = os.path.basename(os.path.normpath(project_path))
    return get_forge(name)


def detect_forge_from_url(url: str) -> ForgeProvider:
    """Infer a ForgeProvider from a URL domain.

    Used when a user pastes a PR/issue URL for a project that is not in
    projects.yaml.  Falls back to GitHubForge for unknown domains.

    Args:
        url: A forge URL (PR, MR, issue, or repo).

    Returns:
        A ForgeProvider whose domain matches the URL.
    """
    if not url:
        return GitHubForge()

    lower = url.lower()

    from urllib.parse import urlparse
    parsed=urlparse(lower)

    netloc = parsed.netloc

    # While this still allows for nefarious github.enterprise.whatever,
    # we presume that is intentional subdomain design in that case
    if netloc.endswith("github.com") or "github.enterprise" in netloc:
        return GitHubForge()

    # Phase 2: self-hosted Gogs — detected by KOAN_GOGS_HOST match
    gogs_host = _gogs_host_for_detection()
    if netloc == gogs_host:
        from app.forge.gogs import GogsForge
        return GogsForge()

    # Phase 3a: gitlab.com and self-hosted GitLab
    # if "gitlab.com" in lower or _is_gitlab_url(lower):
    #     return GitLabForge()

    # Phase 3b: Codeberg / Forgejo / Gitea
    # if "codeberg.org" in lower or "gitea.io" in lower:
    #     return GiteaForge()

    # Unknown domain — default to GitHub to avoid breaking callers.
    return GitHubForge()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_forge_config(project_name: Optional[str]) -> tuple:
    """Read forge type and URL from projects.yaml for the given project.

    Returns:
        (forge_type: str, forge_url: str | None)
    """
    if not project_name:
        return DEFAULT_FORGE, None

    try:
        import os
        from app.projects_config import load_projects_config, get_project_config

        koan_root = os.environ.get("KOAN_ROOT", "")
        if not koan_root:
            log.warning("KOAN_ROOT not set — cannot resolve forge for project %r", project_name)
            return DEFAULT_FORGE, None
        config = load_projects_config(koan_root)
        if not config:
            return DEFAULT_FORGE, None

        project_cfg = get_project_config(config, project_name)
        forge_type = project_cfg.get("forge", DEFAULT_FORGE)
        # Support both 'forge_url' (new) and 'github_url' (legacy alias)
        # TODO make the rest of the project do a similar fallback scheme, no other place is this done
        forge_url = project_cfg.get("forge_url") or project_cfg.get("github_url")
        return forge_type, forge_url

    except Exception:
        log.warning("Failed to resolve forge config for project %r, "
                    "falling back to default", project_name, exc_info=True)
        return DEFAULT_FORGE, None


def _known_forge_types() -> set:
    """Return the set of currently recognised forge type strings."""
    from app.forge.registry import FORGE_TYPES
    return set(FORGE_TYPES.keys())


def _gogs_host_for_detection() -> str:
    """Return the lowercase netloc of KOAN_GOGS_HOST for URL detection.

    Uses urlparse to extract the netloc (host + optional port) so the
    comparison with ``parsed.netloc`` in detect_forge_from_url() is
    consistent regardless of whether the host includes a port number.
    """
    try:
        from urllib.parse import urlparse
        from app.gogs_auth import get_gogs_host
        host = get_gogs_host()
        if not host:
            return ""
        # Ensure the value has a scheme so urlparse extracts netloc correctly.
        if "://" not in host:
            host = f"https://{host}"
        return urlparse(host).netloc.lower()
    except Exception:
        log.warning("Could not resolve Gogs host for URL detection", exc_info=True)
        return ""
