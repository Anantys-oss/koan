"""URL parsing utilities for self-hosted Gogs instances.

Gogs URL shapes:
    PR:    https://<host>/<owner>/<repo>/pulls/<number>
    Issue: https://<host>/<owner>/<repo>/issues/<number>

Unlike GitHub the host is variable (set via KOAN_GOGS_HOST), so patterns
are built at call time rather than compiled as module-level constants.
"""

import re
from typing import Optional, Tuple

from app.gogs_auth import get_gogs_host


def _host_pattern() -> str:
    """Return an escaped regex fragment for the configured Gogs host."""
    host = get_gogs_host()
    if not host:
        raise ValueError(
            "KOAN_GOGS_HOST is not configured. "
            "Set it to your Gogs base URL (e.g. https://git.example.com)."
        )
    return re.escape(host)


def parse_pr_url(url: str) -> Tuple[str, str, str]:
    """Extract (owner, repo, pr_number) from a Gogs PR URL.

    Gogs PR URLs use '/pulls/' (plural), e.g.:
        https://git.example.com/owner/repo/pulls/42

    Args:
        url: Gogs PR URL.

    Returns:
        Tuple of (owner, repo, pr_number) as strings.

    Raises:
        ValueError: If the URL does not match the expected pattern.
    """
    pattern = rf"{_host_pattern()}/([^/]+)/([^/]+)/pulls/(\d+)"
    match = re.match(pattern, url.strip())
    if not match:
        raise ValueError(f"Invalid Gogs PR URL: {url!r}")
    return match.group(1), match.group(2), match.group(3)


def parse_issue_url(url: str) -> Tuple[str, str, str]:
    """Extract (owner, repo, issue_number) from a Gogs issue URL.

    Args:
        url: Gogs issue URL (e.g. https://git.example.com/owner/repo/issues/5).

    Returns:
        Tuple of (owner, repo, issue_number) as strings.

    Raises:
        ValueError: If the URL does not match the expected pattern.
    """
    pattern = rf"{_host_pattern()}/([^/]+)/([^/]+)/issues/(\d+)"
    match = re.match(pattern, url.strip())
    if not match:
        raise ValueError(f"Invalid Gogs issue URL: {url!r}")
    return match.group(1), match.group(2), match.group(3)


def search_pr_url(text: str) -> Tuple[str, str, str]:
    """Search for a Gogs PR URL anywhere in text.

    Args:
        text: Text that may contain a Gogs PR URL.

    Returns:
        Tuple of (owner, repo, pr_number) as strings.

    Raises:
        ValueError: If no PR URL is found in text.
    """
    pattern = rf"{_host_pattern()}/([^/]+)/([^/]+)/pulls/(\d+)"
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"No Gogs PR URL found in: {text!r}")
    return match.group(1), match.group(2), match.group(3)


def search_issue_url(text: str) -> Tuple[str, str, str]:
    """Search for a Gogs issue URL anywhere in text.

    Args:
        text: Text that may contain a Gogs issue URL.

    Returns:
        Tuple of (owner, repo, issue_number) as strings.

    Raises:
        ValueError: If no issue URL is found in text.
    """
    pattern = rf"{_host_pattern()}/([^/]+)/([^/]+)/issues/(\d+)"
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"No Gogs issue URL found in: {text!r}")
    return match.group(1), match.group(2), match.group(3)


def build_pr_url(
    base_url: str, owner: str, repo: str, number: int
) -> str:
    """Build a Gogs PR web URL."""
    return f"{base_url.rstrip('/')}/{owner}/{repo}/pulls/{number}"


def build_issue_url(
    base_url: str, owner: str, repo: str, number: int
) -> str:
    """Build a Gogs issue web URL."""
    return f"{base_url.rstrip('/')}/{owner}/{repo}/issues/{number}"


def is_gogs_url(url: str, base_url: Optional[str] = None) -> bool:
    """Return True if the URL belongs to the configured Gogs instance.

    Args:
        url: URL to check.
        base_url: Optional base URL override (defaults to KOAN_GOGS_HOST).

    Returns:
        True if the URL's host matches the configured Gogs host.
    """
    host = (base_url or get_gogs_host()).rstrip("/")
    if not host or not url:
        return False
    return url.lower().startswith(host.lower())
