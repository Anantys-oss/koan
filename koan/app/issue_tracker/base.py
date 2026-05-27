"""Common interface for issue tracker clients.

Skill code should normally call the provider-neutral service functions in
``app.issue_tracker`` (``fetch_issue``, ``add_comment``, ``create_issue``,
``find_existing_plan_issue``) rather than branching on GitHub vs Jira. Concrete
clients implement this lower-level contract behind that service boundary.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.issue_tracker.types import IssueContent, IssueRef


class IssueTracker(ABC):
    """Provider-neutral issue tracker contract.

    Concrete clients (``GitHubIssueTracker``, ``JiraIssueTracker``) implement
    these methods so callers can perform the required actions — read
    description/comments, add a comment, create issues — without knowing which
    backend is configured for the project.
    """

    #: Backend identifier, e.g. ``"github"`` or ``"jira"``.
    provider: str = ""

    #: Whether ``create_issue`` meaningfully honours ``labels``. GitHub does;
    #: Jira ignores them, so label-specific niceties can be skipped.
    supports_labels: bool = False

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when the tracker knows where to read/create issues."""

    @abstractmethod
    def fetch_issue(self, url: str) -> IssueContent:
        """Fetch an issue's title, body, comments, and state."""

    @abstractmethod
    def add_comment(self, url: str, body: str) -> bool:
        """Post a comment on the issue identified by ``url``."""

    @abstractmethod
    def create_issue(self, title: str, body: str, labels=None) -> str:
        """Create an issue and return its browse URL."""

    @abstractmethod
    def find_existing_plan_issue(self, idea: str) -> Optional[IssueRef]:
        """Return an open issue roughly matching ``idea``, or None."""
