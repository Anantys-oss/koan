"""Tests for the Gogs forge implementation.

Covers:
  - gogs_auth.py  — env var helpers
  - gogs_url_parser.py  — URL parsing with a configured host
  - forge/gogs.py  — GogsForge API operations (HTTP mocked)
  - forge/registry.py  — gogs registered in FORGE_TYPES
  - forge/__init__.py  — detect_forge_from_url picks GogsForge
"""

import json
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.forge.gogs import GogsForge
from app.gogs import normalise_pr as _normalise_pr, split_repo as _split_repo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def forge(monkeypatch):
    monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
    monkeypatch.setenv("KOAN_GOGS_TOKEN", "test-token-abc")
    return GogsForge()


def _mock_response(data, status=200):
    """Return a mock urllib response with JSON body."""
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# gogs_auth
# ---------------------------------------------------------------------------

class TestGogsAuth:
    def test_get_gogs_host_reads_env(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com/")
        from app.gogs_auth import get_gogs_host
        assert get_gogs_host() == "https://git.example.com"

    def test_get_gogs_host_normalizes_no_scheme(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "git.example.com")
        from app.gogs_auth import get_gogs_host
        assert get_gogs_host() == "https://git.example.com"

    def test_get_gogs_host_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        from app.gogs_auth import get_gogs_host
        assert get_gogs_host() == ""

    def test_get_gogs_token_reads_env(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_TOKEN", "tok123")
        from app.gogs_auth import get_gogs_token
        assert get_gogs_token() == "tok123"

    def test_get_gogs_token_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_TOKEN", raising=False)
        from app.gogs_auth import get_gogs_token
        assert get_gogs_token() == ""

    def test_get_gogs_auth_headers_with_token(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_TOKEN", "mytoken")
        from app.gogs_auth import get_gogs_auth_headers
        headers = get_gogs_auth_headers()
        assert headers == {"Authorization": "token mytoken"}

    def test_get_gogs_auth_headers_empty_without_token(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_TOKEN", raising=False)
        from app.gogs_auth import get_gogs_auth_headers
        assert get_gogs_auth_headers() == {}

    def test_is_gogs_configured_true(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        monkeypatch.setenv("KOAN_GOGS_TOKEN", "tok")
        from app.gogs_auth import is_gogs_configured
        assert is_gogs_configured() is True

    def test_is_gogs_configured_false_missing_host(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        monkeypatch.setenv("KOAN_GOGS_TOKEN", "tok")
        from app.gogs_auth import is_gogs_configured
        assert is_gogs_configured() is False

    def test_is_gogs_configured_false_missing_token(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        monkeypatch.delenv("KOAN_GOGS_TOKEN", raising=False)
        from app.gogs_auth import is_gogs_configured
        assert is_gogs_configured() is False


# ---------------------------------------------------------------------------
# gogs_url_parser
# ---------------------------------------------------------------------------

class TestGogsUrlParser:
    def test_parse_pr_url(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import parse_pr_url
        owner, repo, number = parse_pr_url("https://git.example.com/alice/myrepo/pulls/42")
        assert owner == "alice"
        assert repo == "myrepo"
        assert number == "42"

    def test_parse_pr_url_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import parse_pr_url
        with pytest.raises(ValueError):
            parse_pr_url("https://github.com/owner/repo/pull/1")

    def test_parse_issue_url(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import parse_issue_url
        owner, repo, number = parse_issue_url("https://git.example.com/alice/myrepo/issues/7")
        assert owner == "alice"
        assert repo == "myrepo"
        assert number == "7"

    def test_search_pr_url_finds_embedded_url(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import search_pr_url
        text = "See PR at https://git.example.com/alice/repo/pulls/10 for details"
        owner, repo, number = search_pr_url(text)
        assert number == "10"

    def test_search_pr_url_raises_when_not_found(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import search_pr_url
        with pytest.raises(ValueError):
            search_pr_url("no url here")

    def test_search_issue_url_finds_embedded_url(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import search_issue_url
        text = "Fixes https://git.example.com/alice/repo/issues/3"
        _, _, number = search_issue_url(text)
        assert number == "3"

    def test_parse_pr_url_raises_when_host_not_configured(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        from app.gogs_url_parser import parse_pr_url
        with pytest.raises(ValueError, match="KOAN_GOGS_HOST"):
            parse_pr_url("https://git.example.com/alice/repo/pulls/1")

    def test_build_pr_url(self):
        from app.gogs_url_parser import build_pr_url
        url = build_pr_url("https://git.example.com", "alice", "myrepo", 5)
        assert url == "https://git.example.com/alice/myrepo/pulls/5"

    def test_build_issue_url(self):
        from app.gogs_url_parser import build_issue_url
        url = build_issue_url("https://git.example.com", "alice", "myrepo", 3)
        assert url == "https://git.example.com/alice/myrepo/issues/3"

    def test_is_gogs_url_true(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import is_gogs_url
        assert is_gogs_url("https://git.example.com/alice/repo/pulls/1") is True

    def test_is_gogs_url_false_for_other_host(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs_url_parser import is_gogs_url
        assert is_gogs_url("https://github.com/alice/repo/pull/1") is False


# ---------------------------------------------------------------------------
# GogsForge — init and meta
# ---------------------------------------------------------------------------

class TestGogsForgeInit:
    def test_name_attribute(self):
        assert GogsForge.name == "gogs"

    def test_base_url_from_arg(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        forge = GogsForge(base_url="https://git.myorg.com")
        assert forge.base_url == "https://git.myorg.com"

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com/")
        forge = GogsForge()
        assert forge.base_url == "https://git.example.com"

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com/")
        forge = GogsForge()
        assert not forge.base_url.endswith("/")

    def test_cli_name(self, forge):
        assert forge.cli_name() == "gogs"

    def test_supported_features_include_pr_and_issues(self, forge):
        from app.forge.base import FEATURE_ISSUES, FEATURE_PR
        assert forge.supports(FEATURE_PR)
        assert forge.supports(FEATURE_ISSUES)

    def test_unsupported_features(self, forge):
        from app.forge.base import (
            FEATURE_CI_STATUS,
            FEATURE_NOTIFICATIONS,
            FEATURE_REACTIONS,
        )
        assert not forge.supports(FEATURE_CI_STATUS)
        assert not forge.supports(FEATURE_NOTIFICATIONS)
        assert not forge.supports(FEATURE_REACTIONS)

    def test_auth_env_returns_host_and_token(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        monkeypatch.setenv("KOAN_GOGS_TOKEN", "tok123")
        forge = GogsForge()
        env = forge.auth_env()
        assert env["KOAN_GOGS_HOST"] == "https://git.example.com"
        assert env["KOAN_GOGS_TOKEN"] == "tok123"


# ---------------------------------------------------------------------------
# GogsForge — requires host
# ---------------------------------------------------------------------------

class TestGogsForgeRequiresHost:
    def test_api_raises_when_host_not_configured(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        from app.gogs import api
        with pytest.raises(RuntimeError, match="KOAN_GOGS_HOST"):
            api("GET", "repos/owner/repo/pulls")


# ---------------------------------------------------------------------------
# GogsForge — PR operations
# ---------------------------------------------------------------------------

class TestGogsForge_PR:
    def test_pr_create_returns_html_url(self, forge, monkeypatch):
        pr_response = {
            "number": 42,
            "html_url": "https://git.example.com/alice/myrepo/pulls/42",
            "title": "My PR",
        }
        mock_resp = _mock_response(pr_response)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = forge.pr_create(
                title="My PR", body="body text", repo="alice/myrepo", head="feature"
            )
        assert url == "https://git.example.com/alice/myrepo/pulls/42"

    def test_pr_create_falls_back_to_constructed_url(self, forge, monkeypatch):
        pr_response = {"number": 7}  # no html_url
        mock_resp = _mock_response(pr_response)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = forge.pr_create(title="T", body="B", repo="alice/repo")
        assert "pulls/7" in url

    def test_pr_create_raises_on_missing_repo(self, forge):
        with pytest.raises(ValueError, match="owner/repo"):
            forge.pr_create(title="T", body="B", repo=None)

    def test_pr_view_normalises_fields(self, forge):
        raw = {
            "number": 3,
            "title": "A PR",
            "body": "some body",
            "state": "open",
            "head": {"ref": "feature-branch"},
            "base": {"ref": "main"},
            "html_url": "https://git.example.com/alice/repo/pulls/3",
        }
        mock_resp = _mock_response(raw)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = forge.pr_view("alice/repo", 3)
        assert result["headRefName"] == "feature-branch"
        assert result["baseRefName"] == "main"
        assert result["url"] == raw["html_url"]

    def test_list_merged_prs_filters_merged(self, forge):
        pulls = [
            {"merged": True, "head": {"ref": "feat/done"}},
            {"merged": False, "head": {"ref": "feat/open"}},
            {"merged": True, "head": {"ref": "fix/also-done"}},
        ]
        mock_resp = _mock_response(pulls)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            branches = forge.list_merged_prs("alice/repo")
        assert "feat/done" in branches
        assert "fix/also-done" in branches
        assert "feat/open" not in branches

    def test_list_merged_prs_returns_empty_on_bad_response(self, forge):
        mock_resp = _mock_response({})  # not a list
        with patch("urllib.request.urlopen", return_value=mock_resp):
            branches = forge.list_merged_prs("alice/repo")
        assert branches == []

    def test_list_open_pr_branches_returns_head_refs(self, forge):
        pulls = [
            {"head": {"ref": "koan/a"}, "user": {"login": "bot"}},
            {"head": {"ref": "koan/b"}, "user": {"login": "bot"}},
        ]
        mock_resp = _mock_response(pulls)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            branches = forge.list_open_pr_branches("alice/repo")
        assert branches == ["koan/a", "koan/b"]

    def test_list_open_pr_branches_filters_by_author(self, forge):
        pulls = [
            {"head": {"ref": "koan/a"}, "user": {"login": "bot"}},
            {"head": {"ref": "human/x"}, "user": {"login": "alice"}},
        ]
        mock_resp = _mock_response(pulls)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            branches = forge.list_open_pr_branches("alice/repo", author="bot")
        assert branches == ["koan/a"]

    def test_list_open_pr_branches_empty_on_error(self, forge):
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            assert forge.list_open_pr_branches("alice/repo") == []

    def test_find_pr_for_branch_returns_normalised_open_pr(self, forge):
        pulls = [
            {"number": 9, "state": "open", "merged": False,
             "head": {"ref": "koan/feat"},
             "html_url": "https://git.example.com/alice/repo/pulls/9"},
        ]
        mock_resp = _mock_response(pulls)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            pr = forge.find_pr_for_branch("alice/repo", "koan/feat")
        assert pr["number"] == 9
        assert pr["state"] == "OPEN"
        assert pr["isDraft"] is False
        assert pr["url"].endswith("/pulls/9")

    def test_find_pr_for_branch_maps_merged_state(self, forge):
        pulls = [
            {"number": 4, "state": "closed", "merged": True,
             "head": {"ref": "koan/done"}, "html_url": "u"},
        ]
        mock_resp = _mock_response(pulls)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            pr = forge.find_pr_for_branch("alice/repo", "koan/done")
        assert pr["state"] == "MERGED"

    def test_find_pr_for_branch_returns_none_when_absent(self, forge):
        pulls = [{"number": 1, "state": "open", "head": {"ref": "other"}}]
        mock_resp = _mock_response(pulls)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert forge.find_pr_for_branch("alice/repo", "koan/missing") is None

    def test_repo_slug_parses_origin_remote(self, forge, monkeypatch):
        monkeypatch.setattr(
            "app.gogs.owner_repo_from_git_remote",
            lambda path: ("alice", "repo"),
        )
        assert forge.repo_slug("/p") == "alice/repo"

    def test_repo_slug_none_when_no_remote(self, forge, monkeypatch):
        monkeypatch.setattr(
            "app.gogs.owner_repo_from_git_remote",
            lambda path: None,
        )
        assert forge.repo_slug("/p") is None


# ---------------------------------------------------------------------------
# GogsForge — issue operations
# ---------------------------------------------------------------------------

class TestGogsForge_Issues:
    def test_issue_create_in_repo_returns_url(self, forge, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        response = {
            "number": 5,
            "html_url": "https://git.example.com/alice/repo/issues/5",
        }
        mock_resp = _mock_response(response)
        from app.gogs import _issue_create_in_repo
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = _issue_create_in_repo("alice/repo", "Bug found", "details")
        assert url == "https://git.example.com/alice/repo/issues/5"

    def test_issue_create_raises_on_missing_cwd(self, forge):
        with pytest.raises(RuntimeError, match="not a git repository"):
            forge.issue_create("title", "body")

    def test_issue_create_raises_when_repo_unresolvable(self, forge):
        # issue_create derives the repo from the git remote in ``cwd``; with
        # no cwd (and thus no resolvable remote) it cannot determine where to
        # file the issue and raises RuntimeError. Gogs *does* support issues
        # (see test_supported_features_include_pr_and_issues), so this is no
        # longer a NotImplementedError.
        with pytest.raises(RuntimeError):
            forge.issue_create("title", "body")


# ---------------------------------------------------------------------------
# GogsForge — API passthrough
# ---------------------------------------------------------------------------

class TestGogsForge_API:
    def test_run_api_returns_json_string(self, forge):
        data = {"key": "value"}
        mock_resp = _mock_response(data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = forge.run_api("repos/alice/repo/issues")
        assert json.loads(result) == data

    def test_api_raises_runtime_error_on_http_error(self, forge, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.gogs import api
        exc = urllib.error.HTTPError(
            url="http://x", code=404, msg="Not Found", hdrs=None, fp=None
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(RuntimeError, match="HTTP 404"):
                api("GET", "repos/nobody/nope")


# ---------------------------------------------------------------------------
# GogsForge — URL construction
# ---------------------------------------------------------------------------

class TestGogsForge_WebUrl:
    def test_get_web_url_for_pr(self, forge):
        url = forge.get_web_url("alice/repo", "pr", 10)
        assert url == "https://git.example.com/alice/repo/pulls/10"

    def test_get_web_url_for_issue(self, forge):
        url = forge.get_web_url("alice/repo", "issue", 3)
        assert url == "https://git.example.com/alice/repo/issues/3"

    def test_get_web_url_for_pulls_type(self, forge):
        url = forge.get_web_url("alice/repo", "pulls", 5)
        assert url == "https://git.example.com/alice/repo/pulls/5"


# ---------------------------------------------------------------------------
# GogsForge — URL parsing delegation
# ---------------------------------------------------------------------------

class TestGogsForge_UrlParsing:
    def test_parse_pr_url(self, forge, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        owner, repo, number = forge.parse_pr_url(
            "https://git.example.com/alice/myrepo/pulls/42"
        )
        assert owner == "alice"
        assert repo == "myrepo"
        assert number == "42"

    def test_parse_issue_url(self, forge, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        owner, repo, number = forge.parse_issue_url(
            "https://git.example.com/alice/myrepo/issues/7"
        )
        assert number == "7"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestSplitRepo:
    def test_valid_owner_repo(self):
        owner, repo = _split_repo("alice/myrepo")
        assert owner == "alice"
        assert repo == "myrepo"

    def test_raises_on_none(self):
        with pytest.raises(ValueError):
            _split_repo(None)

    def test_raises_on_missing_slash(self):
        with pytest.raises(ValueError):
            _split_repo("noslash")

    def test_raises_on_empty(self):
        with pytest.raises(ValueError):
            _split_repo("")


class TestNormalisePr:
    def test_maps_head_ref(self):
        raw = {"head": {"ref": "feature"}, "base": {"ref": "main"}, "number": 1,
               "title": "t", "body": "b", "state": "open", "html_url": "http://x/1"}
        result = _normalise_pr(raw)
        assert result["headRefName"] == "feature"
        assert result["baseRefName"] == "main"

    def test_handles_missing_head(self):
        result = _normalise_pr({})
        assert result["headRefName"] == ""
        assert result["baseRefName"] == ""


# ---------------------------------------------------------------------------
# Registry — gogs is registered
# ---------------------------------------------------------------------------

class TestGogsInRegistry:
    def test_gogs_in_forge_types(self):
        from app.forge.registry import FORGE_TYPES
        assert "gogs" in FORGE_TYPES

    def test_gogs_forge_class_registered(self):
        from app.forge.registry import FORGE_TYPES
        assert FORGE_TYPES["gogs"] is GogsForge

    def test_get_forge_class_gogs(self):
        from app.forge.registry import get_forge_class
        cls = get_forge_class("gogs")
        assert cls is GogsForge

    def test_all_forge_types_are_subclasses(self):
        from app.forge.base import ForgeProvider
        from app.forge.registry import FORGE_TYPES
        for name, cls in FORGE_TYPES.items():
            assert issubclass(cls, ForgeProvider), (
                f"FORGE_TYPES[{name!r}] is not a ForgeProvider subclass"
            )


# ---------------------------------------------------------------------------
# Factory — detect_forge_from_url picks GogsForge for configured host
# ---------------------------------------------------------------------------

class TestDetectForgeFromUrlGogs:
    def test_gogs_url_returns_gogs_forge(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.forge import detect_forge_from_url
        forge = detect_forge_from_url("https://git.example.com/alice/repo/pulls/1")
        assert isinstance(forge, GogsForge)

    def test_github_url_still_returns_github_forge(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        from app.forge import detect_forge_from_url
        from app.forge.github import GitHubForge
        forge = detect_forge_from_url("https://github.com/alice/repo/pull/1")
        assert isinstance(forge, GitHubForge)

    def test_gogs_not_detected_when_host_not_configured(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        from app.forge import detect_forge_from_url
        from app.forge.github import GitHubForge
        # Falls back to GitHub when KOAN_GOGS_HOST not set
        forge = detect_forge_from_url("https://git.example.com/alice/repo/pulls/1")
        assert isinstance(forge, GitHubForge)


# ---------------------------------------------------------------------------
# get_forge — returns GogsForge when configured in projects.yaml
# ---------------------------------------------------------------------------

class TestGetForgeGogs:
    def test_get_forge_returns_gogs_for_configured_project(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
        monkeypatch.setenv("KOAN_GOGS_TOKEN", "tok")
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        projects_yaml = tmp_path / "projects.yaml"
        projects_yaml.write_text(
            "projects:\n"
            "  my-gogs-project:\n"
            "    path: /tmp/my-gogs-project\n"
            "    forge: gogs\n"
            "    forge_url: https://git.example.com\n"
        )

        from app.forge import get_forge
        forge = get_forge("my-gogs-project")
        assert isinstance(forge, GogsForge)


# ---------------------------------------------------------------------------
# scripts/gogs CLI — repo permissions and fork commands
# ---------------------------------------------------------------------------

import importlib.machinery
import importlib.util
import pathlib
import sys


def _load_gogs_script(monkeypatch):
    """Load scripts/gogs as a module, with env vars set."""
    monkeypatch.setenv("KOAN_GOGS_HOST", "https://git.example.com")
    monkeypatch.setenv("KOAN_GOGS_TOKEN", "test-token")
    script_path = str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "gogs")
    loader = importlib.machinery.SourceFileLoader("gogs_script", script_path)
    spec = importlib.util.spec_from_file_location("gogs_script", script_path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestGogsScriptRepoPermissions:
    def test_permissions_outputs_json(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        repo_data = {
            "permissions": {"admin": False, "push": True, "pull": True},
            "name": "myrepo",
        }
        mock_resp = _mock_response(repo_data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            mod.cmd_repo_permissions(["--repo", "alice/myrepo"])

        out = capsys.readouterr().out
        result = json.loads(out)
        assert result == {"admin": False, "push": True, "pull": True}

    def test_permissions_defaults_falsy_fields(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        # Gogs instance that omits permissions entirely
        repo_data = {"name": "myrepo"}
        mock_resp = _mock_response(repo_data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            mod.cmd_repo_permissions(["--repo", "alice/myrepo"])

        out = capsys.readouterr().out
        result = json.loads(out)
        assert result == {"admin": False, "push": False, "pull": False}

    def test_permissions_jq_filter(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        repo_data = {"permissions": {"admin": True, "push": True, "pull": True}}
        mock_resp = _mock_response(repo_data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            mod.cmd_repo_permissions(["--repo", "alice/myrepo", "--jq", ".admin"])

        out = capsys.readouterr().out.strip()
        assert out == "True"

    def test_permissions_json_field_filter(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        repo_data = {"permissions": {"admin": False, "push": True, "pull": True}}
        mock_resp = _mock_response(repo_data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            mod.cmd_repo_permissions(["--repo", "alice/myrepo", "--json", "push,pull"])

        out = capsys.readouterr().out
        result = json.loads(out)
        assert result == {"push": True, "pull": True}
        assert "admin" not in result


class TestGogsScriptRepoFork:
    def test_fork_returns_html_url(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        fork_data = {
            "html_url": "https://git.example.com/bob/myrepo",
            "full_name": "bob/myrepo",
        }
        mock_resp = _mock_response(fork_data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            mod.cmd_repo_fork(["--repo", "alice/myrepo"])

        out = capsys.readouterr().out.strip()
        assert out == "https://git.example.com/bob/myrepo"

    def test_fork_with_org(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        fork_data = {
            "html_url": "https://git.example.com/myorg/myrepo",
            "full_name": "myorg/myrepo",
        }
        mock_resp = _mock_response(fork_data)

        captured_req = {}

        def _fake_urlopen(req, timeout=30):
            captured_req["data"] = req.data
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            mod.cmd_repo_fork(["--repo", "alice/myrepo", "--org", "myorg"])

        out = capsys.readouterr().out.strip()
        assert out == "https://git.example.com/myorg/myrepo"
        body = json.loads(captured_req["data"])
        assert body["organization"] == "myorg"

    def test_fork_falls_back_to_full_name(self, monkeypatch, capsys):
        mod = _load_gogs_script(monkeypatch)

        # Gogs instance that doesn't return html_url
        fork_data = {"full_name": "bob/myrepo"}
        mock_resp = _mock_response(fork_data)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            mod.cmd_repo_fork(["--repo", "alice/myrepo"])

        out = capsys.readouterr().out.strip()
        assert out == "https://git.example.com/bob/myrepo"
