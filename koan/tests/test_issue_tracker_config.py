"""Tests for provider-neutral issue tracker configuration."""

from pathlib import Path
from unittest.mock import patch

import yaml

import pytest

from app.issue_tracker.config import (
    get_jira_branch_map_for_polling,
    get_jira_project_map_for_polling,
    get_project_issue_tracker,
    get_tracker_for_project,
    detect_legacy_jira_projects,
    find_project_for_jira_key,
    format_legacy_jira_projects_warning,
    normalize_github_repo,
    resolve_code_repository,
    set_project_tracker,
)
from app.projects_config import invalidate_projects_config_cache, load_projects_config


def _write_yaml(root: Path, content: str) -> None:
    (root / "projects.yaml").write_text(content)
    invalidate_projects_config_cache()


class TestIssueTrackerConfig:
    def test_github_tracker_uses_repo_and_default_branch(self):
        config = {
            "projects": {
                "myapp": {
                    "issue_tracker": {
                        "provider": "github",
                        "repo": "https://github.com/acme/myapp.git",
                        "default_branch": "main",
                    }
                }
            }
        }

        tracker = get_project_issue_tracker(config, "myapp")

        assert tracker["provider"] == "github"
        assert tracker["repo"] == "acme/myapp"
        assert tracker["default_branch"] == "main"

    def test_github_tracker_ignores_project_github_url(self):
        """github_url (auto-populated from origin) must NOT be used as the
        tracker repo — it points at the fork in fork workflows. Fork
        detection in resolve_code_repository handles the upstream lookup."""
        config = {
            "projects": {
                "myapp": {
                    "github_url": "git@github.com:acme/myapp.git",
                }
            }
        }

        tracker = get_project_issue_tracker(config, "myapp")

        assert tracker["provider"] == "github"
        assert tracker["repo"] == ""

    def test_jira_tracker_reads_project_key_type_and_branch(self):
        config = {
            "projects": {
                "myapp": {
                    "issue_tracker": {
                        "provider": "jira",
                        "jira_project": "foo",
                        "jira_issue_type": "Story",
                        "default_branch": "release/11.126",
                    }
                }
            }
        }

        tracker = get_project_issue_tracker(config, "myapp")

        assert tracker["provider"] == "jira"
        assert tracker["jira_project"] == "FOO"
        assert tracker["jira_issue_type"] == "Story"
        assert tracker["default_branch"] == "release/11.126"

    def test_projects_yaml_tracker_wins_over_legacy_jira_mapping(self, tmp_path):
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    issue_tracker:
      provider: github
      repo: acme/myapp
""",
        )

        tracker = get_tracker_for_project(
            "myapp",
            koan_root=str(tmp_path),
            legacy_config={"jira": {"projects": {"FOO": "myapp"}}},
        )

        assert tracker["provider"] == "github"
        assert tracker["repo"] == "acme/myapp"

    def test_legacy_jira_mapping_is_ignored_without_projects_yaml(self, tmp_path):
        tracker = get_tracker_for_project(
            "myapp",
            koan_root=str(tmp_path),
            legacy_config={
                "jira": {
                    "projects": {
                        "FOO": {"project": "myapp", "branch": "release/11.126"}
                    }
                }
            },
        )

        assert tracker["provider"] == "github"
        assert tracker["jira_project"] == ""
        assert tracker["default_branch"] == ""

    def test_polling_maps_use_projects_yaml_only(self, tmp_path):
        _write_yaml(
            tmp_path,
            """
projects:
  alpha:
    issue_tracker:
      provider: jira
      jira_project: FOO
      default_branch: release/new
""",
        )
        legacy = {
            "jira": {
                "projects": {
                    "FOO": {"project": "legacy-alpha", "branch": "release/old"},
                    "BAR": {"project": "beta", "branch": "release/beta"},
                }
            }
        }

        assert get_jira_project_map_for_polling(legacy, koan_root=str(tmp_path)) == {
            "FOO": "alpha",
        }
        assert get_jira_branch_map_for_polling(legacy, koan_root=str(tmp_path)) == {
            "FOO": "release/new",
        }

    def test_legacy_jira_mapping_warning_helpers(self):
        legacy = {
            "jira": {
                "projects": {
                    "foo": "alpha",
                    "BAR": {"project": "beta", "branch": "release/beta"},
                }
            }
        }

        keys = detect_legacy_jira_projects(legacy)
        assert keys == ["BAR", "FOO"]
        message = format_legacy_jira_projects_warning(keys)
        assert "ignored" in message
        assert "projects.yaml" in message

    def test_set_project_tracker_persists_jira_section(self, tmp_path):
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    path: /tmp/myapp
""",
        )

        set_project_tracker(
            str(tmp_path),
            "myapp",
            {
                "provider": "jira",
                "jira_project": "FOO",
                "jira_issue_type": "Bug",
                "default_branch": "release/11.126",
            },
        )

        config = load_projects_config(str(tmp_path))
        section = config["projects"]["myapp"]["issue_tracker"]
        assert section == {
            "provider": "jira",
            "jira_project": "FOO",
            "jira_issue_type": "Bug",
            "default_branch": "release/11.126",
        }

    def test_set_project_tracker_invalidates_cache(self, tmp_path):
        """Subsequent reads must see the new tracker without manual invalidation.

        Regression: when the cache invalidation lived in the /tracker skill
        handler, any other caller of set_project_tracker would observe stale
        config until the file's mtime ticked over. Invalidation now lives in
        the library itself.
        """
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    path: /tmp/myapp
    issue_tracker:
      provider: github
      repo: acme/myapp
""",
        )

        # Prime the cache with the original config so a stale read would
        # return the GitHub tracker.
        before = load_projects_config(str(tmp_path))
        assert before["projects"]["myapp"]["issue_tracker"]["provider"] == "github"

        set_project_tracker(
            str(tmp_path),
            "myapp",
            {"provider": "jira", "jira_project": "FOO"},
        )

        # No manual invalidate_projects_config_cache() between the write and
        # the read — set_project_tracker is responsible for it.
        after = load_projects_config(str(tmp_path))
        assert after["projects"]["myapp"]["issue_tracker"]["provider"] == "jira"
        assert after["projects"]["myapp"]["issue_tracker"]["jira_project"] == "FOO"

    def test_resolve_code_repository_prefers_submit_target(self, tmp_path, monkeypatch):
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    submit_to_repository:
      repo: https://github.com/upstream/myapp.git
    issue_tracker:
      provider: jira
      jira_project: FOO
      repo: fork/myapp
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        assert resolve_code_repository("myapp") == "upstream/myapp"


    def test_resolve_code_repository_fork_detection_before_github_url(
        self, tmp_path, monkeypatch,
    ):
        """When github_url points to a fork, resolve_code_repository must
        detect the upstream via resolve_target_repo before falling back
        to the fork's github_url."""
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    github_url: fork-owner/myapp
    path: /some/path
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        monkeypatch.setattr(
            "app.issue_tracker.config.get_project_submit_to_repository",
            lambda *a, **kw: {},
        )

        with patch(
            "app.github.resolve_target_repo",
            return_value="upstream-owner/myapp",
        ):
            result = resolve_code_repository("myapp", "/some/path")
        assert result == "upstream-owner/myapp"

    def test_resolve_code_repository_falls_back_to_github_url_when_no_fork(
        self, tmp_path, monkeypatch,
    ):
        """When resolve_target_repo returns None (not a fork),
        github_url is used as last resort."""
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    github_url: acme/myapp
    path: /some/path
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        with patch("app.github.resolve_target_repo", return_value=None):
            result = resolve_code_repository("myapp", "/some/path")
        assert result == "acme/myapp"


class TestLoadProjectsFallbacks:
    def test_no_root_defaults_to_github(self, monkeypatch):
        """No koan_root arg and no KOAN_ROOT env: resolution still returns a
        usable GitHub default rather than raising."""
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        tracker = get_tracker_for_project("myapp")
        assert tracker["provider"] == "github"
        assert tracker["repo"] == ""

    def test_unreadable_config_defaults_to_github(self, tmp_path, monkeypatch):
        """An OSError/ValueError while loading projects.yaml degrades to the
        GitHub default instead of propagating."""
        monkeypatch.setattr(
            "app.issue_tracker.config.load_projects_config",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")),
        )
        tracker = get_tracker_for_project("myapp", koan_root=str(tmp_path))
        assert tracker["provider"] == "github"


class TestGetProjectIssueTrackerEdgeCases:
    def test_non_dict_issue_tracker_section_uses_defaults(self):
        config = {"projects": {"myapp": {"issue_tracker": "not-a-dict"}}}
        tracker = get_project_issue_tracker(config, "myapp")
        assert tracker["provider"] == "github"
        assert tracker["repo"] == ""

    def test_unknown_provider_falls_back_to_github(self):
        config = {
            "projects": {"myapp": {"issue_tracker": {"provider": "gitlab"}}}
        }
        tracker = get_project_issue_tracker(config, "myapp")
        assert tracker["provider"] == "github"

    def test_blank_issue_type_uses_default(self):
        config = {
            "projects": {
                "myapp": {
                    "issue_tracker": {
                        "provider": "jira",
                        "jira_project": "FOO",
                        "jira_issue_type": "   ",
                    }
                }
            }
        }
        tracker = get_project_issue_tracker(config, "myapp")
        assert tracker["jira_issue_type"] == "Task"


class TestPollingMapsEmptyConfig:
    def test_empty_config_yields_empty_maps(self):
        assert get_jira_project_map_for_polling({}, koan_root="") == {}
        assert get_jira_branch_map_for_polling({}, koan_root="") == {}


class TestFindProjectForJiraKey:
    def test_key_without_dash_returns_empty(self):
        assert find_project_for_jira_key("NODASH") == ""

    def test_empty_key_returns_empty(self):
        assert find_project_for_jira_key("") == ""

    def test_resolves_to_project(self, tmp_path):
        _write_yaml(
            tmp_path,
            """
projects:
  alpha:
    issue_tracker:
      provider: jira
      jira_project: FOO
""",
        )
        assert find_project_for_jira_key("FOO-123", koan_root=str(tmp_path)) == "alpha"

    def test_unmapped_key_returns_empty(self, tmp_path):
        _write_yaml(tmp_path, "projects: {}\n")
        assert find_project_for_jira_key("BAR-9", koan_root=str(tmp_path)) == ""


class TestLegacyJiraHelperEdgeCases:
    def test_detect_non_dict_projects_returns_empty(self):
        assert detect_legacy_jira_projects({"jira": {"projects": ["FOO"]}}) == []

    def test_format_empty_keys_returns_empty_string(self):
        assert format_legacy_jira_projects_warning([]) == ""


class TestResolveCodeRepositoryFallbacks:
    def test_tracker_repo_used_when_no_submit_target(self, tmp_path, monkeypatch):
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    issue_tracker:
      provider: github
      repo: acme/myapp
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        # No project_path: skip fork detection, fall through to tracker repo.
        assert resolve_code_repository("myapp") == "acme/myapp"

    def test_fork_detection_error_falls_through(self, tmp_path, monkeypatch):
        """A failure inside resolve_target_repo must not crash resolution; it
        falls through to github_url."""
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    github_url: acme/myapp
    path: /some/path
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.github.resolve_target_repo", side_effect=RuntimeError("nope")):
            assert resolve_code_repository("myapp", "/some/path") == "acme/myapp"

    def test_origin_repo_last_resort(self, tmp_path, monkeypatch):
        """With no submit target, no tracker repo, and no github_url, the
        origin remote is the final fallback."""
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    path: /some/path
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.github.resolve_target_repo", return_value=None), patch(
            "app.github.origin_repo", return_value="acme/myapp"
        ):
            assert resolve_code_repository("myapp", "/some/path") == "acme/myapp"

    def test_origin_repo_error_returns_empty(self, tmp_path, monkeypatch):
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
    path: /some/path
""",
        )
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        with patch("app.github.resolve_target_repo", return_value=None), patch(
            "app.github.origin_repo", side_effect=OSError("git missing")
        ):
            assert resolve_code_repository("myapp", "/some/path") == ""

    def test_no_config_no_path_returns_empty(self, monkeypatch):
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        assert resolve_code_repository("myapp") == ""


class TestSetProjectTrackerValidation:
    def test_rejects_unknown_provider(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported issue tracker provider"):
            set_project_tracker(str(tmp_path), "myapp", {"provider": "gitlab"})

    def test_jira_requires_project_key(self, tmp_path):
        with pytest.raises(ValueError, match="jira_project is required"):
            set_project_tracker(str(tmp_path), "myapp", {"provider": "jira"})

    def test_none_project_entry_is_replaced(self, tmp_path):
        _write_yaml(
            tmp_path,
            """
projects:
  myapp:
""",
        )
        set_project_tracker(
            str(tmp_path), "myapp", {"provider": "github", "repo": "acme/myapp"}
        )
        config = load_projects_config(str(tmp_path))
        assert config["projects"]["myapp"]["issue_tracker"]["repo"] == "acme/myapp"


def test_normalize_github_repo_accepts_owner_repo_and_urls():
    assert normalize_github_repo("acme/myapp") == "acme/myapp"
    assert normalize_github_repo("https://github.com/acme/myapp.git") == "acme/myapp"
    assert normalize_github_repo("git@github.com:acme/myapp.git") == "acme/myapp"


def test_projects_yaml_written_as_mapping(tmp_path):
    set_project_tracker(
        str(tmp_path),
        "myapp",
        {"provider": "github", "repo": "acme/myapp"},
    )

    data = yaml.safe_load((tmp_path / "projects.yaml").read_text())
    assert data["projects"]["myapp"]["issue_tracker"] == {
        "provider": "github",
        "repo": "acme/myapp",
    }
