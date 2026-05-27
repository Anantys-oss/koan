"""GitHub issue tracker client."""

import json
import re
import sys
from typing import Optional

from app.github import (
    api,
    fetch_issue_state,
    fetch_issue_with_comments,
    sanitize_github_comment,
)
from app.github_url_parser import parse_github_url
from app.issue_tracker.base import IssueTracker
from app.issue_tracker.config import normalize_github_repo, resolve_code_repository
from app.issue_tracker.types import IssueContent, IssueRef


class GitHubIssueTracker(IssueTracker):
    """Provider-neutral wrapper around the existing GitHub helpers."""

    provider = "github"
    supports_labels = True

    def __init__(
        self,
        project_name: str = "",
        project_path: str = "",
        repo: str = "",
        default_branch: Optional[str] = None,
    ):
        self.project_name = project_name
        self.project_path = project_path
        self.repo = normalize_github_repo(repo) if repo else ""
        self.default_branch = default_branch

    def _target_repo(self) -> str:
        return self.repo or resolve_code_repository(
            self.project_name, self.project_path,
        )

    def is_configured(self) -> bool:
        return bool(self._target_repo())

    def fetch_issue(self, url: str) -> IssueContent:
        owner, repo, url_type, number = parse_github_url(url)
        title, body, comments = fetch_issue_with_comments(owner, repo, number)
        state = fetch_issue_state(owner, repo, number)
        ref = IssueRef(
            provider="github",
            url=url,
            key=str(number),
            project_name=self.project_name,
            repo=f"{owner}/{repo}",
            url_type=url_type,
            default_branch=self.default_branch,
        )
        return IssueContent(ref=ref, title=title, body=body, comments=comments, state=state)

    def add_comment(self, url: str, body: str) -> bool:
        owner, repo, _url_type, number = parse_github_url(url)
        api(
            f"repos/{owner}/{repo}/issues/{number}/comments",
            input_data=sanitize_github_comment(body),
        )
        return True

    def create_issue(self, title: str, body: str, labels=None) -> str:
        from app.github import issue_create

        return issue_create(
            title=title,
            body=body,
            labels=labels,
            repo=self._target_repo() or None,
            cwd=self.project_path or None,
        )

    def find_existing_plan_issue(self, idea: str) -> Optional[IssueRef]:
        repo = self._target_repo()
        if not repo:
            return None
        keywords = _extract_search_keywords(idea)
        if not keywords:
            return None
        query = f"repo:{repo} is:issue is:open {keywords}"
        try:
            raw = api(
                "search/issues",
                extra_args=[
                    "--jq", ".items[:5] | [.[] | {number, title, html_url}]",
                    "-f", f"q={query}",
                    "-f", "per_page=5",
                ],
            )
            results = json.loads(raw)
        except Exception as e:
            print(
                f"[issue_tracker.github] plan-issue search failed: {e}",
                file=sys.stderr,
            )
            return None
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        number = str(first.get("number", ""))
        url = first.get("html_url") or f"https://github.com/{repo}/issues/{number}"
        return IssueRef(
            provider="github",
            url=url,
            key=number,
            project_name=self.project_name,
            repo=repo,
            default_branch=self.default_branch,
        )


def _extract_search_keywords(idea: str) -> str:
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "to", "of", "in", "for", "on",
        "with", "at", "by", "from", "as", "into", "about", "and", "but",
        "or", "not", "no", "that", "this", "it", "we", "our", "you",
        "your", "need", "want", "add", "make", "use",
    }
    words = re.findall(r"\b[a-zA-Z]{2,}\b", (idea or "").lower())
    return " ".join(w for w in words if w not in stop_words)[:80]
