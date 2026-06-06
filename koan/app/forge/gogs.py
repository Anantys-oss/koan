"""Gogs forge implementation — thin delegation wrapper over app.gogs.

GogsForge delegates all operations to the app.gogs module, mirroring
the GitHubForge pattern (which delegates to app.github).  No API logic
lives here; app.gogs is the single implementation source.

Supported features:
    FEATURE_PR        — create, view, list merged/open PRs
    FEATURE_ISSUES    — create issues

Not supported (Gogs API limitation or out of scope):
    FEATURE_CI_STATUS          — Gogs has no native CI API
    FEATURE_REACTIONS          — Gogs does not expose reaction endpoints
    FEATURE_NOTIFICATIONS      — handled by polling, not forge API
    FEATURE_PR_REVIEW_COMMENTS — Gogs PR review API is limited
"""

from typing import Dict, List, Optional, Tuple

from app.forge.base import FEATURE_ISSUES, FEATURE_PR, ForgeProvider


class GogsForge(ForgeProvider):
    """Forge implementation for self-hosted Gogs instances.

    Delegates to app.gogs for all API logic.  The scripts/gogs CLI
    provides a gh-compatible interface for humans.

    Args:
        base_url: Gogs base URL.  Defaults to KOAN_GOGS_HOST env var.
    """

    name = "gogs"

    _SUPPORTED_FEATURES = frozenset({FEATURE_PR, FEATURE_ISSUES})

    def __init__(self, base_url: str = ""):
        from app.gogs_auth import get_gogs_host
        self.base_url = (base_url or get_gogs_host()).rstrip("/")

    # ------------------------------------------------------------------
    # CLI availability (optional scripts/gogs wrapper for human use)
    # ------------------------------------------------------------------

    def cli_name(self) -> str:
        return "gogs"

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def auth_env(self) -> Dict[str, str]:
        from app.gogs_auth import get_gogs_host, get_gogs_token
        env = {}
        host = get_gogs_host()
        token = get_gogs_token()
        if host:
            env["KOAN_GOGS_HOST"] = host
        if token:
            env["KOAN_GOGS_TOKEN"] = token
        return env

    # ------------------------------------------------------------------
    # URL parsing
    # ------------------------------------------------------------------

    def parse_pr_url(self, url: str) -> Tuple[str, str, str]:
        from app.gogs_url_parser import parse_pr_url
        return parse_pr_url(url)

    def parse_issue_url(self, url: str) -> Tuple[str, str, str]:
        from app.gogs_url_parser import parse_issue_url
        return parse_issue_url(url)

    def search_pr_url(self, text: str) -> Tuple[str, str, str]:
        from app.gogs_url_parser import search_pr_url
        return search_pr_url(text)

    def search_issue_url(self, text: str) -> Tuple[str, str, str]:
        from app.gogs_url_parser import search_issue_url
        return search_issue_url(text)

    # ------------------------------------------------------------------
    # PR operations
    # ------------------------------------------------------------------

    def pr_create(
        self,
        title: str,
        body: str,
        draft: bool = True,
        base: Optional[str] = None,
        repo: Optional[str] = None,
        head: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> str:
        from app.gogs import pr_create
        return pr_create(title, body, draft=draft, base=base, repo=repo,
                         head=head, cwd=cwd, base_url=self.base_url)

    def pr_view(
        self,
        repo: str,
        number: int,
        cwd: Optional[str] = None,
    ) -> Dict:
        from app.gogs import pr_view
        return pr_view(repo, number, cwd=cwd, base_url=self.base_url)

    def pr_diff(
        self,
        repo: str,
        number: int,
        cwd: Optional[str] = None,
    ) -> str:
        from app.gogs import pr_diff
        return pr_diff(repo, number, cwd=cwd, base_url=self.base_url)

    def list_merged_prs(
        self,
        repo: str,
        cwd: Optional[str] = None,
    ) -> List[str]:
        from app.gogs import list_merged_prs
        return list_merged_prs(repo, cwd=cwd, base_url=self.base_url)

    def list_open_pr_branches(
        self,
        repo: str,
        author: str = "",
        cwd: Optional[str] = None,
    ) -> List[str]:
        from app.gogs import list_open_pr_branches
        return list_open_pr_branches(repo, author=author, cwd=cwd,
                                     base_url=self.base_url)

    def find_pr_for_branch(
        self,
        repo: str,
        branch: str,
        cwd: Optional[str] = None,
    ) -> Optional[Dict]:
        from app.gogs import find_pr_for_branch
        return find_pr_for_branch(repo, branch, cwd=cwd, base_url=self.base_url)

    # ------------------------------------------------------------------
    # Issue operations
    # ------------------------------------------------------------------

    def issue_create(
        self,
        title: str,
        body: str,
        labels: Optional[List[str]] = None,
        cwd: Optional[str] = None,
    ) -> str:
        from app.gogs import issue_create
        return issue_create(title, body, labels=labels, cwd=cwd,
                            base_url=self.base_url)

    # ------------------------------------------------------------------
    # API access
    # ------------------------------------------------------------------

    def run_api(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict] = None,
        cwd: Optional[str] = None,
    ) -> str:
        import json
        from app.gogs import api
        result = api(method, endpoint, data, base_url=self.base_url)
        return json.dumps(result)

    # ------------------------------------------------------------------
    # Repository introspection
    # ------------------------------------------------------------------

    def get_web_url(
        self,
        repo: str,
        url_type: str,
        number: int,
    ) -> str:
        from app.gogs import split_repo
        owner, repo_name = split_repo(repo)
        path_map = {
            "pull": "pulls",
            "pr": "pulls",
            "pulls": "pulls",
            "issues": "issues",
            "issue": "issues",
        }
        path = path_map.get(url_type, url_type)
        return f"{self.base_url}/{owner}/{repo_name}/{path}/{number}"

    def detect_fork(self, project_path: str) -> Optional[str]:
        from app.gogs import detect_fork
        return detect_fork(project_path, base_url=self.base_url)

    def repo_slug(self, project_path: str) -> Optional[str]:
        from app.gogs import repo_slug
        return repo_slug(project_path)

    # ------------------------------------------------------------------
    # Feature matrix
    # ------------------------------------------------------------------

    def supports(self, feature: str) -> bool:
        return feature in self._SUPPORTED_FEATURES
