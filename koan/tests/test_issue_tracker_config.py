"""Tests for provider-neutral issue tracker configuration."""

from pathlib import Path

import yaml

from app.issue_tracker.config import (
    get_jira_branch_map_for_polling,
    get_jira_project_map_for_polling,
    get_project_issue_tracker,
    get_tracker_for_project,
    detect_legacy_jira_projects,
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

        from unittest.mock import patch

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

        from unittest.mock import patch

        with patch("app.github.resolve_target_repo", return_value=None):
            result = resolve_code_repository("myapp", "/some/path")
        assert result == "acme/myapp"


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
