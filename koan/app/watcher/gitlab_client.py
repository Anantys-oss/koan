"""GitLab read-only client — polling via python-gitlab.

Provides group project listing, recent commits, MRs, and issues retrieval.
"""

import logging
import os

import gitlab

from app.watcher.helpers import get_watcher_config

logger = logging.getLogger("watcher.gitlab_client")


class GitLabClient:
    """Read-only GitLab client for polling group activity."""

    def __init__(self, gl: gitlab.Gitlab, group: str):
        self._gl = gl
        self._group = group

    @classmethod
    def from_config(cls, config: dict) -> "GitLabClient":
        """Create a GitLabClient from watcher config.

        Token is loaded from environment variable specified in config.
        """
        gitlab_config = config.get("gitlab", {})
        token_env = gitlab_config.get("token_env", "GITLAB_TOKEN")
        token = os.environ.get(token_env, "")
        group = gitlab_config.get("group", "yourart")

        if not token:
            raise ValueError(f"GitLab token not found in env var {token_env}")

        gl = gitlab.Gitlab("https://gitlab.com", private_token=token)
        return cls(gl, group)

    def list_group_projects(self) -> list[dict]:
        """List all projects in the group, ordered by last activity.

        Returns list of dicts with: id, name, path, last_activity_at,
        default_branch, web_url.
        """
        try:
            logger.info("Listing projects for group '%s'...", self._group)
            group = self._gl.groups.get(self._group)
            projects = group.projects.list(
                order_by="last_activity_at",
                sort="desc",
                per_page=100,
                get_all=True,
            )
            logger.info("Found %d projects in group '%s'", len(projects), self._group)
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "path": p.path_with_namespace,
                    "last_activity_at": p.last_activity_at,
                    "default_branch": getattr(p, "default_branch", "main"),
                    "web_url": p.web_url,
                }
                for p in projects
            ]
        except gitlab.exceptions.GitlabError as e:
            logger.error("Failed to list group projects for '%s': %s (type: %s)", self._group, e, type(e).__name__)
            return []
        except Exception as e:
            logger.error("Unexpected error listing group projects: %s (type: %s)", e, type(e).__name__)
            return []

    def get_recent_commits(self, project_id: int, since: str | None = None,
                           branch: str | None = None) -> list[dict]:
        """Get recent commits for a project.

        Args:
            project_id: GitLab project ID.
            since: ISO 8601 timestamp — only commits after this time.
            branch: Branch name (default: project default branch).
        """
        try:
            project = self._gl.projects.get(project_id)
            kwargs = {"per_page": 50}
            if since:
                kwargs["since"] = since
            if branch:
                kwargs["ref_name"] = branch

            commits = project.commits.list(**kwargs)
            return [
                {
                    "id": c.id,
                    "short_id": c.short_id,
                    "title": c.title,
                    "message": c.message,
                    "author_name": c.author_name,
                    "author_email": c.author_email,
                    "created_at": c.created_at,
                    "web_url": c.web_url,
                }
                for c in commits
            ]
        except gitlab.exceptions.GitlabError as e:
            logger.error("Failed to get commits for project %s: %s", project_id, e)
            return []

    def get_recent_merge_requests(self, since: str | None = None) -> list[dict]:
        """Get recent merge requests for the group.

        Args:
            since: ISO 8601 timestamp — only MRs updated after this time.
        """
        try:
            group = self._gl.groups.get(self._group)
            kwargs = {"scope": "all", "per_page": 50, "order_by": "updated_at"}
            if since:
                kwargs["updated_after"] = since

            mrs = group.mergerequests.list(**kwargs)
            return [
                {
                    "iid": mr.iid,
                    "title": mr.title,
                    "state": mr.state,
                    "author": {
                        "username": mr.author.get("username", "unknown") if isinstance(mr.author, dict) else "unknown",
                    },
                    "source_branch": mr.source_branch,
                    "target_branch": mr.target_branch,
                    "web_url": mr.web_url,
                    "created_at": mr.created_at,
                    "updated_at": mr.updated_at,
                }
                for mr in mrs
            ]
        except gitlab.exceptions.GitlabError as e:
            logger.error("Failed to get merge requests: %s", e)
            return []

    def get_recent_issues(self, since: str | None = None) -> list[dict]:
        """Get recent issues for the group.

        Args:
            since: ISO 8601 timestamp — only issues updated after this time.
        """
        try:
            group = self._gl.groups.get(self._group)
            kwargs = {"scope": "all", "per_page": 50, "order_by": "updated_at"}
            if since:
                kwargs["updated_after"] = since

            issues = group.issues.list(**kwargs)
            return [
                {
                    "iid": issue.iid,
                    "title": issue.title,
                    "state": issue.state,
                    "author": {
                        "username": issue.author.get("username", "unknown") if isinstance(issue.author, dict) else "unknown",
                    },
                    "web_url": issue.web_url,
                    "created_at": issue.created_at,
                    "updated_at": issue.updated_at,
                }
                for issue in issues
            ]
        except gitlab.exceptions.GitlabError as e:
            logger.error("Failed to get issues: %s", e)
            return []
