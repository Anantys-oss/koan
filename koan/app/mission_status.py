"""GitHub "Running" indicator: koan:working label + koan/mission commit status.

While a GitHub-linked mission runs, Kōan surfaces a live indicator with no
GitHub App, reusing the existing ``gh`` auth:

- an issue **label** (``koan:working``) toggled on the linked issue for the
  whole run — the primary live signal, since the issue is known at start; and
- a **commit status** (``context=koan/mission``) posted ``pending`` on the
  pushed branch head at first push and resolved ``success``/``failure``/
  ``error`` at finalize.

This module owns the cross-stage bookkeeping (which SHA/issue a mission maps
to, and what has already been posted) via ``instance/.running-indicator.json``,
keyed by mission title. Every public entrypoint is best-effort: no failure
escapes into the mission lifecycle. Local-only missions (no issue URL in the
text and no ``github_url`` configured) are a silent no-op.

See ``specs/components/git-github.md`` (Mission status indicators) for the
contract, and ``docs/architecture/github-and-trackers.md`` for the flow.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from app import github, utils
from app.github_url_parser import search_issue_url
from app.run_log import log_safe as log

_TRACKER = ".running-indicator.json"
_CONTEXT = "koan/mission"
_DESC = "Kōan is working on this mission"

# owner/repo out of any github(.com|GHE) URL, ignoring a trailing path/.git
_REPO_URL_RE = re.compile(r"https?://[^/]*github[^/]*/([^/\s]+)/([^/\s#?]+)")


def _tracker_path(instance: str) -> str:
    return os.path.join(instance, _TRACKER)


def _load(instance: str) -> dict:
    try:
        with open(_tracker_path(instance), encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(instance: str, data: dict) -> None:
    utils.atomic_write_json(Path(_tracker_path(instance)), data)


def _resolve_config(project_name: str) -> dict:
    """Resolve the per-project running-indicator config, falling back to global."""
    try:
        from app.projects_config import (
            get_project_running_indicator,
            load_projects_config,
        )
        cfg = load_projects_config(os.environ.get("KOAN_ROOT", ""))
        if cfg:
            return get_project_running_indicator(cfg, project_name)
    except Exception:
        pass
    from app.config import get_running_indicator_config
    return get_running_indicator_config()


def _repo_from_github_url(url: str) -> Optional[str]:
    """Extract ``owner/repo`` from a project's configured github_url."""
    if not url:
        return None
    match = _REPO_URL_RE.search(url)
    if not match:
        return None
    repo = match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{match.group(1)}/{repo}"


def _resolve_link(instance: str, mission_title: str,
                  project_name: str) -> Optional[dict]:
    """Derive ``{repo, issue}`` for a mission.

    Priority: an issue URL embedded in the mission text (gives both repo and
    issue number), then the project's ``github_url`` (repo only, no issue).
    Returns ``None`` for local-only / non-GitHub missions.
    """
    try:
        owner, name, num = search_issue_url(mission_title)
        return {"repo": f"{owner}/{name}", "issue": num}
    except ValueError:
        pass
    try:
        from app.projects_config import get_project_config, load_projects_config
        cfg = load_projects_config(os.environ.get("KOAN_ROOT", ""))
        if cfg:
            url = (get_project_config(cfg, project_name) or {}).get("github_url")
            repo = _repo_from_github_url(url or "")
            if repo:
                return {"repo": repo, "issue": None}
    except Exception:
        pass
    return None


def start_indicator(instance: str, mission_title: str,
                    project_name: str = "") -> None:
    """Raise the live indicator at the confirmed Pending→In Progress transition.

    Sets the ``koan:working`` label on the linked issue (when there is one) and
    records a tracker entry so a later push / finalize can complete the flow.
    Best-effort — never blocks the mission.
    """
    cfg = _resolve_config(project_name)
    if not cfg.get("enabled"):
        return
    try:
        link = _resolve_link(instance, mission_title, project_name)
        if not link:
            return  # local-only / non-GitHub mission
        entry = {"repo": link["repo"], "issue": link["issue"],
                 "sha": None, "branch": None, "project": project_name}
        if cfg.get("issue_label") and link["issue"]:
            github.ensure_label(link["repo"], cfg["label_name"])
            github.add_issue_label(link["repo"], link["issue"], cfg["label_name"])
        data = _load(instance)
        data[mission_title] = entry
        _save(instance, data)
    except Exception as e:  # best-effort — never block the mission
        log("koan", f"running-indicator start skipped: {e}")


def on_branch_pushed(instance: str, project_name: str, repo: str,
                     branch: str, sha: str) -> None:
    """Post the ``pending`` commit status when a mission branch is first pushed.

    The tracker entry created at start has no SHA yet (koan pushes late); this
    fills it in, matched by project (only one main-loop mission runs at a time).
    """
    cfg = _resolve_config(project_name)
    if not cfg.get("enabled") or not cfg.get("commit_status"):
        return
    try:
        data = _load(instance)
        # Match the single active main-loop mission for this project that has
        # not yet had a SHA recorded.
        title = next(
            (t for t, e in data.items()
             if e.get("project") == project_name and not e.get("sha")),
            None,
        )
        if title is None:
            return
        data[title].update(sha=sha, branch=branch, repo=repo)
        _save(instance, data)
        github.set_commit_status(repo, sha, "pending", context=_CONTEXT,
                                 description=_DESC)
    except Exception as e:
        log("koan", f"running-indicator push skipped: {e}")


def resolve_indicator(instance: str, mission_title: str, *,
                      success: bool, state: Optional[str] = None) -> None:
    """Lower the indicator on a terminal transition.

    Posts the final commit status (green/red, or an explicit ``state``) when a
    SHA was recorded, removes the ``koan:working`` label, and drops the tracker
    entry. Best-effort — never blocks finalization.
    """
    try:
        data = _load(instance)
        entry = data.pop(mission_title, None)
        if entry is None:
            return
        _save(instance, data)
        cfg = _resolve_config(entry.get("project") or "")
        gh_state = state or ("success" if success else "failure")
        if entry.get("sha") and cfg.get("commit_status"):
            github.set_commit_status(entry["repo"], entry["sha"], gh_state,
                                     context=_CONTEXT, description=_DESC)
        if entry.get("issue") and cfg.get("issue_label"):
            github.remove_issue_label(entry["repo"], entry["issue"],
                                      cfg["label_name"])
    except Exception as e:
        log("koan", f"running-indicator resolve skipped: {e}")


def reconcile_stale_indicators(instance: str, active_titles) -> None:
    """Resolve any tracked mission no longer active as an ``error`` (crash net).

    A hard crash skips ``resolve_indicator``, stranding a yellow ``pending``.
    Called at startup with the set of currently Pending/In Progress mission
    keys (canonicalized); anything tracked but not active is torn down.
    """
    try:
        from app.missions import canonical_mission_key
    except Exception:
        canonical_mission_key = None

    def _key(title: str) -> str:
        if canonical_mission_key is None:
            return re.sub(r"\s+", " ", title).strip()
        return re.sub(r"\s+", " ", canonical_mission_key(title)).strip()

    active = {_key(t) for t in (active_titles or set())}
    data = _load(instance)
    for title in [t for t in data if _key(t) not in active]:
        log("koan", f"running-indicator: reconciling orphan {title[:50]!r}")
        resolve_indicator(instance, title, success=False, state="error")
