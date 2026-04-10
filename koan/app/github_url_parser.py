"""GitHub and Jira URL parsing utilities.

Provides centralized parsing for GitHub PR/issue URLs and Jira issue URLs
with consistent error handling and validation.
"""

import re
from typing import Optional, Tuple

# GitHub URL patterns
PR_URL_PATTERN = r'https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)'
ISSUE_URL_PATTERN = r'https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)'
PR_OR_ISSUE_PATTERN = r'https?://github\.com/([^/]+)/([^/]+)/(pull|issues)/(\d+)'

# Jira URL pattern: https://org.atlassian.net/browse/PROJ-123
JIRA_ISSUE_URL_PATTERN = r'https?://[^/]+\.atlassian\.net/browse/([A-Z][A-Z0-9]+-\d+)'


def _clean_url(url: str) -> str:
    """Clean a URL by removing fragments and whitespace.
    
    Args:
        url: The URL to clean
        
    Returns:
        Cleaned URL without fragment or surrounding whitespace
    """
    return url.split("#")[0].strip()


def parse_pr_url(url: str) -> Tuple[str, str, str]:
    """Extract owner, repo, and PR number from a GitHub PR URL.

    Args:
        url: GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)

    Returns:
        Tuple of (owner, repo, pr_number) as strings

    Raises:
        ValueError: If the URL doesn't match expected PR format
    """
    clean_url = _clean_url(url)
    match = re.match(PR_URL_PATTERN, clean_url)
    if not match:
        raise ValueError(f"Invalid PR URL: {url}")
    return match.group(1), match.group(2), match.group(3)


def parse_issue_url(url: str) -> Tuple[str, str, str]:
    """Extract owner, repo, and issue number from a GitHub issue URL.

    Args:
        url: GitHub issue URL (e.g., https://github.com/owner/repo/issues/123)

    Returns:
        Tuple of (owner, repo, issue_number) as strings

    Raises:
        ValueError: If the URL doesn't match expected issue format
    """
    clean_url = _clean_url(url)
    match = re.match(ISSUE_URL_PATTERN, clean_url)
    if not match:
        raise ValueError(f"Invalid issue URL: {url}")
    return match.group(1), match.group(2), match.group(3)


def search_pr_url(text: str) -> Tuple[str, str, str]:
    """Search for a GitHub PR URL anywhere in text.

    Unlike parse_pr_url which expects the URL at the start, this searches
    the entire string for an embedded PR URL.

    Args:
        text: Text that may contain a GitHub PR URL

    Returns:
        Tuple of (owner, repo, pr_number) as strings

    Raises:
        ValueError: If no PR URL is found in text
    """
    match = re.search(PR_URL_PATTERN, text)
    if not match:
        raise ValueError(f"No PR URL found in: {text}")
    return match.group(1), match.group(2), match.group(3)


def search_issue_url(text: str) -> Tuple[str, str, str]:
    """Search for a GitHub issue URL anywhere in text.

    Unlike parse_issue_url which expects the URL at the start, this searches
    the entire string for an embedded issue URL.

    Args:
        text: Text that may contain a GitHub issue URL

    Returns:
        Tuple of (owner, repo, issue_number) as strings

    Raises:
        ValueError: If no issue URL is found in text
    """
    match = re.search(ISSUE_URL_PATTERN, text)
    if not match:
        raise ValueError(f"No issue URL found in: {text}")
    return match.group(1), match.group(2), match.group(3)


def parse_github_url(url: str) -> Tuple[str, str, str, str]:
    """Extract owner, repo, type, and number from a GitHub PR or issue URL.

    Args:
        url: GitHub PR or issue URL

    Returns:
        Tuple of (owner, repo, url_type, number) where url_type is 'pull' or 'issues'

    Raises:
        ValueError: If the URL doesn't match expected format
    """
    clean_url = _clean_url(url)
    match = re.match(PR_OR_ISSUE_PATTERN, clean_url)
    if not match:
        raise ValueError(f"Invalid GitHub URL: {url}")
    return match.group(1), match.group(2), match.group(3), match.group(4)


# --- Jira URL helpers ---

def is_jira_url(url: str) -> bool:
    """Check whether a URL is a Jira issue URL."""
    return bool(re.search(JIRA_ISSUE_URL_PATTERN, _clean_url(url)))


def parse_jira_url(url: str) -> str:
    """Extract the issue key from a Jira browse URL.

    Args:
        url: Jira issue URL (e.g. https://org.atlassian.net/browse/PROJ-123)

    Returns:
        Issue key (e.g. "PROJ-123")

    Raises:
        ValueError: If the URL doesn't match expected Jira format
    """
    clean_url = _clean_url(url)
    match = re.search(JIRA_ISSUE_URL_PATTERN, clean_url)
    if not match:
        raise ValueError(f"Invalid Jira URL: {url}")
    return match.group(1)


def search_jira_url(text: str) -> Optional[Tuple[str, str]]:
    """Search for a Jira issue URL anywhere in text.

    Returns:
        Tuple of (full_url, issue_key) or None if not found.
    """
    match = re.search(JIRA_ISSUE_URL_PATTERN, text)
    if not match:
        return None
    return match.group(0), match.group(1)
