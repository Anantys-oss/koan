"""Tests for koan/app/branch_limiter.py — branch saturation limiter."""

from unittest.mock import MagicMock, patch

from app.branch_limiter import (
    _get_open_pr_branches,
    count_pending_branches,
)


class TestCountPendingBranches:
    """Tests for count_pending_branches() — union of local + PR branches."""

    @patch("app.branch_limiter._get_open_pr_branches")
    @patch("app.branch_limiter._get_local_unmerged_branches")
    def test_union_deduplicates(self, mock_local, mock_pr):
        """Branch with both local copy and open PR counted once."""
        mock_local.return_value = {"koan/fix-a", "koan/fix-b"}
        mock_pr.return_value = {"koan/fix-b", "koan/fix-c"}

        count = count_pending_branches(
            "/instance", "myapp", "/code/myapp", ["owner/myapp"], "bot",
        )
        assert count == 3  # fix-a, fix-b, fix-c

    @patch("app.branch_limiter._get_open_pr_branches")
    @patch("app.branch_limiter._get_local_unmerged_branches")
    def test_local_only(self, mock_local, mock_pr):
        """No GitHub URLs — count only local branches."""
        mock_local.return_value = {"koan/fix-a", "koan/fix-b"}
        mock_pr.return_value = set()

        count = count_pending_branches(
            "/instance", "myapp", "/code/myapp", [], "bot",
        )
        assert count == 2

    @patch("app.branch_limiter._get_open_pr_branches")
    @patch("app.branch_limiter._get_local_unmerged_branches")
    def test_pr_only(self, mock_local, mock_pr):
        """No local branches — count only PR branches."""
        mock_local.return_value = set()
        mock_pr.return_value = {"koan/fix-a"}

        count = count_pending_branches(
            "/instance", "myapp", "/code/myapp", ["owner/myapp"], "bot",
        )
        assert count == 1

    @patch("app.branch_limiter._get_open_pr_branches")
    @patch("app.branch_limiter._get_local_unmerged_branches")
    def test_empty_both(self, mock_local, mock_pr):
        """No branches at all."""
        mock_local.return_value = set()
        mock_pr.return_value = set()

        count = count_pending_branches(
            "/instance", "myapp", "/code/myapp", ["owner/myapp"], "bot",
        )
        assert count == 0

    @patch("app.branch_limiter._get_open_pr_branches")
    @patch("app.branch_limiter._get_local_unmerged_branches")
    def test_github_error_falls_back_to_local(self, mock_local, mock_pr):
        """GitHub API error → local-only count."""
        mock_local.return_value = {"koan/fix-a", "koan/fix-b"}
        mock_pr.return_value = set()  # Empty on error (handled internally)

        count = count_pending_branches(
            "/instance", "myapp", "/code/myapp", ["owner/myapp"], "bot",
        )
        assert count == 2


class TestGetOpenPrBranchesForge:
    """_get_open_pr_branches routes through the project's forge."""

    def test_github_forge_iterates_configured_urls(self):
        forge = MagicMock()
        forge.name = "github"
        with patch("app.forge.get_forge", return_value=forge), \
             patch("app.github.list_open_pr_branches",
                   side_effect=[["koan/a"], ["koan/b"]]) as mock_list:
            result = _get_open_pr_branches(
                "myapp", "/code/myapp", ["o/r1", "o/r2"], "bot",
            )
        assert result == {"koan/a", "koan/b"}
        assert mock_list.call_count == 2

    def test_non_github_forge_uses_repo_slug(self):
        """Gogs etc.: resolve the slug from the checkout and ask the forge —
        the configured github_urls are ignored on non-GitHub forges."""
        forge = MagicMock()
        forge.name = "gogs"
        forge.repo_slug.return_value = "alice/repo"
        forge.list_open_pr_branches.return_value = ["koan/x", "koan/y"]
        with patch("app.forge.get_forge", return_value=forge):
            result = _get_open_pr_branches(
                "myapp", "/code/myapp", ["ignored-url"], "bot",
            )
        assert result == {"koan/x", "koan/y"}
        forge.repo_slug.assert_called_once_with("/code/myapp")
        forge.list_open_pr_branches.assert_called_once_with(
            "alice/repo", "bot", cwd="/code/myapp",
        )

    def test_no_author_returns_empty(self):
        # Without an author there's nothing to attribute PRs to.
        assert _get_open_pr_branches("myapp", "/code/myapp", ["o/r"], "") == set()
