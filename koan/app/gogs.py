"""Gogs API client — low-level operations for self-hosted Gogs instances.

This module mirrors the role of app.github for the Gogs forge.
forge/gogs.py is a thin delegation wrapper over these functions.

Env vars:
    KOAN_GOGS_HOST   — Base URL of the Gogs instance (e.g. https://git.example.com)
    KOAN_GOGS_TOKEN  — Personal access token for authentication
"""

import json
import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def split_repo(repo: Optional[str]) -> Tuple[str, str]:
    """Split an owner/repo string into (owner, repo_name).

    Raises:
        ValueError: If repo is empty or not in owner/repo format.
    """
    if not repo:
        raise ValueError("repo must be specified in owner/repo format")
    parts = repo.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid repo format: {repo!r} (expected owner/repo)")
    return parts[0], parts[1]


def normalise_pr(data: Dict) -> Dict:
    """Map Gogs PR API fields to GitHub-compatible field names."""
    return {
        "number": data.get("number"),
        "title": data.get("title", ""),
        "body": data.get("body", ""),
        "state": data.get("state", ""),
        "headRefName": (data.get("head") or {}).get("ref", ""),
        "baseRefName": (data.get("base") or {}).get("ref", ""),
        "url": data.get("html_url", ""),
    }


def pr_author(pr: Dict) -> str:
    """Return the login of a Gogs PR's author.

    Gogs exposes the author under ``user`` (and historically ``poster``);
    fall back across both so author filtering works on either API version.
    """
    for key in ("user", "poster"):
        node = pr.get(key)
        if isinstance(node, dict):
            login = node.get("login") or node.get("username") or ""
            if login:
                return login
    return ""


def owner_repo_from_git_remote(project_path: str) -> Optional[Tuple[str, str]]:
    """Parse the git origin remote to extract (owner, repo_name)."""
    if not project_path:
        return None
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=project_path, stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    # SSH: git@host:owner/repo.git  or  git@host:owner/repo
    # HTTPS: https://host/owner/repo.git  or  https://host/owner/repo
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1), match.group(2)
    return None


def _resolve_base_url(base_url: str) -> str:
    """Return base URL from arg or KOAN_GOGS_HOST, stripped of trailing slash."""
    if base_url:
        return base_url.rstrip("/")
    from app.gogs_auth import get_gogs_host
    return get_gogs_host()


def api(
    method: str,
    path: str,
    data: Optional[Dict] = None,
    params: Optional[Dict] = None,
    timeout: int = 30,
    base_url: str = "",
):
    """Make an authenticated Gogs API v1 request.

    Args:
        method: HTTP method (GET, POST, PATCH, DELETE).
        path: API path relative to /api/v1/ (e.g. "$owner/$repo/pulls").
        data: Optional JSON payload for POST/PATCH.
        params: Optional query-string parameters for GET.
        timeout: Request timeout in seconds.
        base_url: Override for KOAN_GOGS_HOST.

    Returns:
        Parsed JSON response (dict or list).

    Raises:
        RuntimeError: On HTTP error or if KOAN_GOGS_HOST is not set.
    """
    resolved = _resolve_base_url(base_url)
    if not resolved:
        raise RuntimeError(
            "Gogs host is not configured. "
            "Set KOAN_GOGS_HOST to your Gogs base URL "
            "(e.g. https://git.example.com)."
        )

    from app.gogs_auth import get_gogs_token

    url = f"{resolved}/api/v1/{path.lstrip('/')}"
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    token = get_gogs_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"

    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Gogs API {method} {path} failed: HTTP {exc.code}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Gogs API {method} {path} error: {exc}"
        ) from exc


def raw_get(url: str, timeout: int = 30) -> str:
    """Fetch a raw URL (non-JSON) with token auth."""
    from app.gogs_auth import get_gogs_host, get_gogs_token

    if not get_gogs_host():
        raise RuntimeError(
            "Gogs host is not configured. Set KOAN_GOGS_HOST."
        )

    token = get_gogs_token()
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Gogs fetch {url} failed: HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"Gogs fetch {url} error: {exc}") from exc


def pr_create(
    title: str,
    body: str,
    draft: bool = True,
    base: Optional[str] = None,
    repo: Optional[str] = None,
    head: Optional[str] = None,
    cwd: Optional[str] = None,
    base_url: str = "",
) -> str:
    """Create a pull request on the Gogs instance.

    Note: Gogs does not support draft PRs — the ``draft`` flag is
    accepted for interface compatibility but has no effect.

    Returns:
        URL of the created PR.

    Raises:
        ValueError: If ``repo`` is not provided.
        RuntimeError: If the API call fails or token is not configured.
    """
    from app.gogs_auth import get_gogs_token
    if not get_gogs_token():
        raise RuntimeError(
            "GOGS token is not configured. Set KOAN_GOGS_TOKEN to a personal access token."
        )
    resolved = _resolve_base_url(base_url)
    owner, repo_name = split_repo(repo)
    payload: Dict = {"title": title, "body": body or ""}
    if base:
        payload["base"] = base
    if head:
        payload["head"] = head

    data = api("POST", f"{owner}/{repo_name}/pulls", payload, base_url=resolved)
    html_url = data.get("html_url") or ""
    if not html_url:
        number = data.get("number")
        if not number:
            raise RuntimeError("Could not determine created PR's URL!")
        html_url = f"{resolved}/{owner}/{repo_name}/pulls/{number}"
    return html_url


def pr_view(
    repo: str,
    number: int,
    cwd: Optional[str] = None,
    base_url: str = "",
) -> Dict:
    """Return a normalised dict for a single PR."""
    owner, repo_name = split_repo(repo)
    data = api("GET", f"{owner}/{repo_name}/pulls/{number}", base_url=base_url)
    return normalise_pr(data)


def pr_diff(
    repo: str,
    number: int,
    cwd: Optional[str] = None,
    base_url: str = "",
) -> str:
    """Fetch the unified diff for a Gogs PR via the web endpoint."""
    resolved = _resolve_base_url(base_url)
    owner, repo_name = split_repo(repo)
    url = f"{resolved}/{owner}/{repo_name}/pulls/{number}.diff"
    return raw_get(url)


def list_merged_prs(
    repo: str,
    cwd: Optional[str] = None,
    base_url: str = "",
) -> List[str]:
    """Return head-ref branch names of merged PRs in ``repo``."""
    owner, repo_name = split_repo(repo)
    items = api(
        "GET",
        f"{owner}/{repo_name}/pulls",
        params={"state": "closed", "limit": "50"},
        base_url=base_url,
    )
    if not isinstance(items, list):
        return []
    return [
        pr.get("head", {}).get("ref", "")
        for pr in items
        if isinstance(pr, dict) and pr.get("merged")
    ]


def list_open_pr_branches(
    repo: str,
    author: str = "",
    cwd: Optional[str] = None,
    base_url: str = "",
) -> List[str]:
    """Return head-ref branch names of open PRs in ``repo``.

    Best-effort: returns an empty list on any API error.
    """
    try:
        owner, repo_name = split_repo(repo)
        items = api(
            "GET",
            f"{owner}/{repo_name}/pulls",
            params={"state": "open", "limit": "50"},
            base_url=base_url,
        )
    except (RuntimeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    branches = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if author and pr_author(item) != author:
            continue
        ref = (item.get("head") or {}).get("ref", "")
        if ref:
            branches.add(ref)
    return sorted(branches)


def find_pr_for_branch(
    repo: str,
    branch: str,
    cwd: Optional[str] = None,
    base_url: str = "",
) -> Optional[Dict]:
    """Return the PR whose head ref is ``branch``, or None.

    State is normalised to GitHub's upper-case convention
    ("OPEN"/"CLOSED"/"MERGED") so callers can compare uniformly across
    forges.  Gogs has no draft PRs, so ``isDraft`` is always False.
    """
    try:
        owner, repo_name = split_repo(repo)
        items = api(
            "GET",
            f"{owner}/{repo_name}/pulls",
            params={"state": "all", "limit": "50"},
            base_url=base_url,
        )
    except (RuntimeError, ValueError):
        return None
    if not isinstance(items, list):
        return None
    for pr in items:
        if not isinstance(pr, dict):
            continue
        if (pr.get("head") or {}).get("ref", "") != branch:
            continue
        if pr.get("merged"):
            state = "MERGED"
        elif (pr.get("state") or "").lower() == "closed":
            state = "CLOSED"
        else:
            state = "OPEN"
        return {
            "number": pr.get("number"),
            "state": state,
            "isDraft": False,
            "url": pr.get("html_url", ""),
            "headRefName": branch,
        }
    return None


def _issue_create_in_repo(
    repo: str,
    title: str,
    body: str,
    labels: Optional[List[str]] = None,
    base_url: str = "",
) -> str:
    """Create an issue in an explicitly specified repo.

    Args:
        repo: Repository in owner/repo format.
        title: Issue title.
        body: Issue body (markdown).
        labels: Optional list of label names (currently ignored; Gogs label
                API uses IDs, not names).
        base_url: Override for KOAN_GOGS_HOST.

    Returns:
        URL of the created issue.
    """
    from app.gogs_auth import get_gogs_token
    if not get_gogs_token():
        raise RuntimeError(
            "GOGS token is not configured. Set KOAN_GOGS_TOKEN to a personal access token."
        )
    resolved = _resolve_base_url(base_url)
    owner, repo_name = split_repo(repo)
    payload: Dict = {"title": title, "body": body or ""}
    data = api("POST", f"{owner}/{repo_name}/issues", payload, base_url=resolved)
    html_url = data.get("html_url") or ""
    if not html_url:
        number = data.get("number")
        html_url = f"{resolved}/{owner}/{repo_name}/issues/{number}"
    return html_url


def issue_create(
    title: str,
    body: str,
    labels: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    base_url: str = "",
) -> str:
    """Create an issue in the repo at ``cwd``.

    Derives the owner/repo from the git origin remote of ``cwd``.

    Raises:
        RuntimeError: If ``cwd`` has no git origin or token is not set.
    """
    from app.gogs_auth import get_gogs_token
    if not get_gogs_token():
        raise RuntimeError(
            "GOGS token is not configured. Set KOAN_GOGS_TOKEN to a personal access token."
        )
    result = owner_repo_from_git_remote(cwd)
    if not result:
        raise RuntimeError(
            f"{cwd} is not a git repository with a configured origin remote"
        )
    owner, repo_name = result
    return _issue_create_in_repo(f"{owner}/{repo_name}", title, body, labels, base_url=base_url)


def detect_fork(project_path: str, base_url: str = "") -> Optional[str]:
    """Detect if a Gogs repo is a fork and return the parent owner/repo.

    Returns None when not a fork or on any error.
    """
    owner_repo = owner_repo_from_git_remote(project_path)
    if not owner_repo:
        return None
    owner, repo_name = owner_repo
    try:
        data = api("GET", f"{owner}/{repo_name}", base_url=base_url)
        parent = data.get("parent")
        if parent and isinstance(parent, dict):
            p_owner = parent.get("owner", {}).get("login", "")
            p_name = parent.get("name", "")
            if p_owner and p_name:
                return f"{p_owner}/{p_name}"
    except (RuntimeError, KeyError, AttributeError, TypeError) as exc:
        log.warning("Gogs fork detection failed for %s: %s", project_path, exc)
    return None


def repo_slug(project_path: str) -> Optional[str]:
    """Return ``owner/repo`` parsed from the origin git remote, or None."""
    result = owner_repo_from_git_remote(project_path)
    if not result:
        return None
    owner, repo_name = result
    return f"{owner}/{repo_name}"
