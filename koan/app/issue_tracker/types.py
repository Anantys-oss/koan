"""Shared issue tracker value objects."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class IssueRef:
    """Provider-neutral reference to an issue or PR-like tracker item."""

    provider: str
    url: str
    key: str
    project_name: str = ""
    repo: str = ""
    url_type: str = "issue"
    default_branch: Optional[str] = None
    issue_type: str = "Task"

    @property
    def label(self) -> str:
        if self.provider == "github" and self.key:
            return f"#{self.key}"
        return self.key


@dataclass
class IssueContent:
    """Fetched tracker content normalized for skill prompts."""

    ref: IssueRef
    title: str
    body: str
    comments: List[Dict[str, str]] = field(default_factory=list)
    state: str = "open"

