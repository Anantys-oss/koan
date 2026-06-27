"""Project-registry assembly for the dashboard (no Flask).

Composes existing helpers into per-project "card" dicts for the ``/projects``
welcome screen. Best-effort everywhere: a missing config or inaccessible path
yields safe defaults (empty strings, zero counts, ``last_activity=None``) and
never raises.
"""
from __future__ import annotations

import logging

from app.dashboard import state
from app.dashboard_service import read_file
from app.missions import group_by_project
from app.projects_config import (
    get_project_cli_provider,
    get_project_config,
    get_project_models,
    load_projects_config,
)
from app.utils import get_known_projects

logger = logging.getLogger(__name__)


def _project_counts() -> dict:
    """Map project name -> {'pending': int, 'in_progress': int}."""
    content = read_file(state.MISSIONS_FILE)
    out: dict = {}
    if not content:
        return out
    for pname, pdata in group_by_project(content).items():
        out[pname] = {
            "pending": len(pdata.get("pending", [])),
            "in_progress": len(pdata.get("in_progress", [])),
        }
    return out


def _last_activity(project: str) -> float | None:
    """Newest mtime among this project's journal files, or None.

    Best-effort: any filesystem error yields None ("N/A" in the UI). No
    subprocess is spawned, so there is nothing to time out.
    """
    jdir = state.INSTANCE_DIR / "journal"
    newest: float | None = None
    try:
        for day in jdir.iterdir():
            f = day / f"{project}.md"
            if f.is_file():
                m = f.stat().st_mtime
                if newest is None or m > newest:
                    newest = m
    except OSError:
        logger.debug(
            "last-activity scan failed for project %r under %s",
            project, jdir, exc_info=True,
        )
        return None
    return newest


def _checklist(github_url: str, provider: str) -> list:
    """Flag missing recommended config as actionable checklist items."""
    items = []
    if not github_url:
        items.append({
            "key": "github_url",
            "label": "Missing GitHub URL",
            "fix": "edit_config",
        })
    if not provider:
        items.append({
            "key": "cli_provider",
            "label": "No CLI provider set (uses global default)",
            "fix": "edit_config",
            "severity": "info",
        })
    return items


def _card(name: str, path: str, counts: dict, cfg) -> dict:
    github_url, provider, model = "", "", ""
    if cfg:
        try:
            proj_cfg = get_project_config(cfg, name)
            github_url = str(proj_cfg.get("github_url", "") or "")
            provider = get_project_cli_provider(cfg, name)
            models = get_project_models(cfg, name)
            model = str(models.get("mission", "") or "")
        except Exception:  # noqa: BLE001 - registry must never raise
            logger.warning(
                "Failed to read config for project %r; using empty defaults",
                name, exc_info=True,
            )
    c = counts.get(name, {})
    return {
        "name": name,
        "path": path,
        "github_url": github_url,
        "cli_provider": provider,
        "model": model,
        "pending": c.get("pending", 0),
        "in_progress": c.get("in_progress", 0),
        "last_activity": _last_activity(name),
        "checklist": _checklist(github_url, provider),
    }


def build_project_registry() -> list:
    """One card dict per known project, sorted case-insensitively by name."""
    cfg = load_projects_config(str(state.KOAN_ROOT)) or None
    counts = _project_counts()
    cards = [
        _card(name, path, counts, cfg)
        for name, path in get_known_projects()
    ]
    return sorted(cards, key=lambda c: c["name"].lower())


def build_project_status(name: str) -> dict:
    """Single card for one project (async refresh / unknown-project safe).

    The returned card carries a ``found`` flag so callers can distinguish an
    unknown/typo'd project name from a genuinely idle one.
    """
    cfg = load_projects_config(str(state.KOAN_ROOT)) or None
    path = ""
    found = False
    for pname, ppath in get_known_projects():
        if pname.lower() == name.lower():
            path = ppath
            found = True
            break
    card = _card(name, path, _project_counts(), cfg)
    card["found"] = found
    return card
