"""Tests for head_tracker.py — remote HEAD change detection."""

import json
import time
from pathlib import Path
from unittest.mock import patch, call

import pytest

from app.head_tracker import (
    HeadChange,
    MIN_CHECK_INTERVAL_HOURS,
    TRACKER_FILE,
    _get_local_head_ref,
    _get_remote_head,
    _load_tracker,
    _save_tracker,
    _update_local_head,
    check_all_projects,
    check_project_head,
    format_changes_report,
)


# --- _get_remote_head ---


class TestGetRemoteHead:
    def test_parses_symref_output(self):
        ls_remote_output = "ref: refs/heads/main\tHEAD\nabc123\tHEAD"
        with patch("app.head_tracker.run_git", return_value=(0, ls_remote_output, "")):
            assert _get_remote_head("origin", "/proj") == "main"

    def test_parses_master_branch(self):
        ls_remote_output = "ref: refs/heads/master\tHEAD\nabc123\tHEAD"
        with patch("app.head_tracker.run_git", return_value=(0, ls_remote_output, "")):
            assert _get_remote_head("origin", "/proj") == "master"

    def test_returns_none_on_failure(self):
        with patch("app.head_tracker.run_git", return_value=(1, "", "fatal")):
            assert _get_remote_head("origin", "/proj") is None

    def test_returns_none_on_empty_output(self):
        with patch("app.head_tracker.run_git", return_value=(0, "", "")):
            assert _get_remote_head("origin", "/proj") is None

    def test_ignores_non_ref_lines(self):
        output = "abc123\tHEAD"
        with patch("app.head_tracker.run_git", return_value=(0, output, "")):
            assert _get_remote_head("origin", "/proj") is None


# --- _get_local_head_ref ---


class TestGetLocalHeadRef:
    def test_parses_symbolic_ref(self):
        with patch(
            "app.head_tracker.run_git",
            return_value=(0, "refs/remotes/origin/master", ""),
        ):
            assert _get_local_head_ref("origin", "/proj") == "master"

    def test_returns_none_on_failure(self):
        with patch("app.head_tracker.run_git", return_value=(1, "", "not a symbolic ref")):
            assert _get_local_head_ref("origin", "/proj") is None


# --- Tracker persistence ---


class TestTrackerPersistence:
    def test_load_missing_file(self, tmp_path):
        assert _load_tracker(str(tmp_path)) == {}

    def test_load_corrupt_json(self, tmp_path):
        (tmp_path / TRACKER_FILE).write_text("not json")
        assert _load_tracker(str(tmp_path)) == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"myproj": {"head_branch": "main", "last_check": 123}}
        _save_tracker(str(tmp_path), data)
        loaded = _load_tracker(str(tmp_path))
        assert loaded == data


# --- check_project_head ---


class TestCheckProjectHead:
    def test_detects_change_master_to_main(self, tmp_path):
        instance_dir = str(tmp_path)
        # Seed tracker with known head = master
        tracker_data = {
            "myproj": {"head_branch": "master", "last_check": 0}
        }
        (tmp_path / TRACKER_FILE).write_text(json.dumps(tracker_data))

        with patch("app.head_tracker._get_remote_head", return_value="main"), \
             patch("app.head_tracker._get_local_head_ref", return_value="master"), \
             patch("app.head_tracker._update_local_head", return_value=None):
            change = check_project_head(
                "myproj", "/proj", "origin", instance_dir, force=True
            )

        assert change is not None
        assert change.old_branch == "master"
        assert change.new_branch == "main"
        assert change.updated is True
        assert change.error is None

    def test_no_change_returns_none(self, tmp_path):
        instance_dir = str(tmp_path)
        tracker_data = {
            "myproj": {"head_branch": "main", "last_check": 0}
        }
        (tmp_path / TRACKER_FILE).write_text(json.dumps(tracker_data))

        with patch("app.head_tracker._get_remote_head", return_value="main"), \
             patch("app.head_tracker._get_local_head_ref", return_value="main"):
            change = check_project_head(
                "myproj", "/proj", "origin", instance_dir, force=True
            )

        assert change is None

    def test_throttled_when_recently_checked(self, tmp_path):
        instance_dir = str(tmp_path)
        tracker_data = {
            "myproj": {"head_branch": "main", "last_check": time.time()}
        }
        (tmp_path / TRACKER_FILE).write_text(json.dumps(tracker_data))

        with patch("app.head_tracker._get_remote_head") as mock_remote:
            change = check_project_head(
                "myproj", "/proj", "origin", instance_dir, force=False
            )

        assert change is None
        mock_remote.assert_not_called()

    def test_force_bypasses_throttle(self, tmp_path):
        instance_dir = str(tmp_path)
        tracker_data = {
            "myproj": {"head_branch": "main", "last_check": time.time()}
        }
        (tmp_path / TRACKER_FILE).write_text(json.dumps(tracker_data))

        with patch("app.head_tracker._get_remote_head", return_value="main"), \
             patch("app.head_tracker._get_local_head_ref", return_value="main"):
            change = check_project_head(
                "myproj", "/proj", "origin", instance_dir, force=True
            )

        assert change is None  # no change, but remote was queried

    def test_first_check_seeds_tracker(self, tmp_path):
        instance_dir = str(tmp_path)

        with patch("app.head_tracker._get_remote_head", return_value="main"), \
             patch("app.head_tracker._get_local_head_ref", return_value=None):
            change = check_project_head(
                "newproj", "/proj", "origin", instance_dir, force=True
            )

        assert change is None
        tracker = _load_tracker(instance_dir)
        assert tracker["newproj"]["head_branch"] == "main"

    def test_update_failure_records_error(self, tmp_path):
        instance_dir = str(tmp_path)
        tracker_data = {
            "myproj": {"head_branch": "master", "last_check": 0}
        }
        (tmp_path / TRACKER_FILE).write_text(json.dumps(tracker_data))

        with patch("app.head_tracker._get_remote_head", return_value="main"), \
             patch("app.head_tracker._get_local_head_ref", return_value="master"), \
             patch("app.head_tracker._update_local_head", return_value="set-head failed: err"):
            change = check_project_head(
                "myproj", "/proj", "origin", instance_dir, force=True
            )

        assert change is not None
        assert change.updated is False
        assert "set-head failed" in change.error

    def test_remote_query_failure_returns_none(self, tmp_path):
        instance_dir = str(tmp_path)

        with patch("app.head_tracker._get_remote_head", return_value=None):
            change = check_project_head(
                "myproj", "/proj", "origin", instance_dir, force=True
            )

        assert change is None


# --- _update_local_head ---


class TestUpdateLocalHead:
    def test_full_update_flow(self):
        calls = []

        def mock_run_git(*args, cwd=None, timeout=30):
            cmd = args[:3]
            calls.append(args)
            if args[:2] == ("remote", "set-head"):
                return (0, "", "")
            if args[0] == "fetch":
                return (0, "", "")
            if args[:2] == ("rev-parse", "--verify"):
                return (0, "abc123", "")
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return (0, "master", "")
            if args[0] == "checkout":
                return (0, "", "")
            if args[0] == "merge":
                return (0, "", "")
            return (0, "", "")

        with patch("app.head_tracker.run_git", side_effect=mock_run_git):
            error = _update_local_head("origin", "main", "master", "/proj")

        assert error is None

    def test_set_head_failure(self):
        with patch(
            "app.head_tracker.run_git",
            return_value=(1, "", "error: could not set-head"),
        ):
            error = _update_local_head("origin", "main", "master", "/proj")
        assert error is not None
        assert "set-head failed" in error

    def test_creates_local_branch_when_missing(self):
        call_log = []

        def mock_run_git(*args, cwd=None, timeout=30):
            call_log.append(args)
            if args[:2] == ("remote", "set-head"):
                return (0, "", "")
            if args[0] == "fetch":
                return (0, "", "")
            if args[:2] == ("rev-parse", "--verify"):
                return (1, "", "not found")
            if args[:2] == ("branch", "--track"):
                return (0, "", "")
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return (0, "some-feature", "")
            return (0, "", "")

        with patch("app.head_tracker.run_git", side_effect=mock_run_git):
            error = _update_local_head("origin", "main", "master", "/proj")

        assert error is None
        branch_create_calls = [c for c in call_log if c[:2] == ("branch", "--track")]
        assert len(branch_create_calls) == 1

    def test_skips_checkout_when_not_on_old_branch(self):
        call_log = []

        def mock_run_git(*args, cwd=None, timeout=30):
            call_log.append(args)
            if args[:2] == ("remote", "set-head"):
                return (0, "", "")
            if args[0] == "fetch":
                return (0, "", "")
            if args[:2] == ("rev-parse", "--verify"):
                return (0, "abc123", "")
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return (0, "some-feature", "")
            return (0, "", "")

        with patch("app.head_tracker.run_git", side_effect=mock_run_git):
            error = _update_local_head("origin", "main", "master", "/proj")

        assert error is None
        checkout_calls = [c for c in call_log if c[0] == "checkout"]
        assert len(checkout_calls) == 0


# --- check_all_projects ---


class TestCheckAllProjects:
    def test_checks_all_git_projects(self, tmp_path):
        # Create fake .git dirs
        (tmp_path / "proj1" / ".git").mkdir(parents=True)
        (tmp_path / "proj2" / ".git").mkdir(parents=True)

        projects = [
            ("proj1", str(tmp_path / "proj1")),
            ("proj2", str(tmp_path / "proj2")),
        ]

        with patch("app.git_prep.get_upstream_remote", return_value="origin"), \
             patch("app.head_tracker.check_project_head", return_value=None) as mock_check:
            changes = check_all_projects(
                projects, str(tmp_path), str(tmp_path), force=True
            )

        assert len(changes) == 0
        assert mock_check.call_count == 2

    def test_skips_non_git_directories(self, tmp_path):
        (tmp_path / "not-git").mkdir()

        projects = [("not-git", str(tmp_path / "not-git"))]

        with patch("app.head_tracker.check_project_head") as mock_check:
            changes = check_all_projects(
                projects, str(tmp_path), str(tmp_path), force=True
            )

        mock_check.assert_not_called()

    def test_collects_changes(self, tmp_path):
        (tmp_path / "proj1" / ".git").mkdir(parents=True)

        projects = [("proj1", str(tmp_path / "proj1"))]
        change = HeadChange("proj1", "origin", "master", "main", updated=True)

        with patch("app.git_prep.get_upstream_remote", return_value="origin"), \
             patch("app.head_tracker.check_project_head", return_value=change):
            changes = check_all_projects(
                projects, str(tmp_path), str(tmp_path), force=True
            )

        assert len(changes) == 1
        assert changes[0].new_branch == "main"

    def test_handles_exception_gracefully(self, tmp_path):
        (tmp_path / "proj1" / ".git").mkdir(parents=True)

        projects = [("proj1", str(tmp_path / "proj1"))]

        with patch("app.git_prep.get_upstream_remote", side_effect=Exception("boom")):
            changes = check_all_projects(
                projects, str(tmp_path), str(tmp_path), force=True
            )

        assert len(changes) == 0


# --- format_changes_report ---


class TestFormatChangesReport:
    def test_no_changes(self):
        assert "No remote HEAD changes" in format_changes_report([])

    def test_successful_change(self):
        changes = [HeadChange("myproj", "origin", "master", "main", updated=True)]
        report = format_changes_report(changes)
        assert "master → main" in report
        assert "updated" in report

    def test_failed_change(self):
        changes = [
            HeadChange("myproj", "origin", "master", "main", updated=False, error="fetch failed")
        ]
        report = format_changes_report(changes)
        assert "FAILED" in report
        assert "fetch failed" in report

    def test_multiple_changes(self):
        changes = [
            HeadChange("proj1", "origin", "master", "main", updated=True),
            HeadChange("proj2", "upstream", "dev", "develop", updated=True),
        ]
        report = format_changes_report(changes)
        assert "(2)" in report
        assert "proj1" in report
        assert "proj2" in report
