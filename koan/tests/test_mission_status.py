"""Tests for app.mission_status — GitHub 'Running' indicator orchestrator."""

import json

import pytest


def _cfg(**over):
    base = {"enabled": True, "commit_status": True,
            "issue_label": True, "label_name": "koan:working"}
    base.update(over)
    return base


def _tracker(tmp_path):
    return tmp_path / ".running-indicator.json"


# ---------------------------------------------------------------------------
# Config gating
# ---------------------------------------------------------------------------


def test_start_disabled_is_noop(tmp_path, monkeypatch):
    import app.mission_status as ms

    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg(enabled=False))
    monkeypatch.setattr(ms, "_resolve_link",
                        lambda i, t, p: {"repo": "o/r", "issue": "7"})
    ms.start_indicator(str(tmp_path), "mission", "proj")
    assert not _tracker(tmp_path).exists()


def test_start_local_only_is_noop(tmp_path, monkeypatch):
    import app.mission_status as ms

    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    monkeypatch.setattr(ms, "_resolve_link", lambda i, t, p: None)
    ms.start_indicator(str(tmp_path), "local mission", "proj")
    assert not _tracker(tmp_path).exists()


# ---------------------------------------------------------------------------
# Linkage resolution
# ---------------------------------------------------------------------------


def test_resolve_link_from_issue_url(tmp_path, monkeypatch):
    import app.mission_status as ms

    text = "Fix https://github.com/acme/widgets/issues/12 please"
    link = ms._resolve_link(str(tmp_path), text, "proj")
    assert link == {"repo": "acme/widgets", "issue": "12"}


def test_resolve_link_from_github_url(tmp_path, monkeypatch):
    import app.mission_status as ms

    def fake_load(_root):
        return {"projects": {"proj": {"github_url": "https://github.com/acme/tool.git"}}}

    monkeypatch.setattr("app.projects_config.load_projects_config", fake_load)
    monkeypatch.setattr("app.projects_config.get_project_config",
                        lambda cfg, name: cfg["projects"][name])
    link = ms._resolve_link(str(tmp_path), "no issue url here", "proj")
    assert link == {"repo": "acme/tool", "issue": None}


def test_resolve_link_none_for_local(tmp_path, monkeypatch):
    import app.mission_status as ms

    monkeypatch.setattr("app.projects_config.load_projects_config",
                        lambda _root: {"projects": {"proj": {}}})
    monkeypatch.setattr("app.projects_config.get_project_config",
                        lambda cfg, name: {})
    assert ms._resolve_link(str(tmp_path), "just text", "proj") is None


# ---------------------------------------------------------------------------
# start -> track
# ---------------------------------------------------------------------------


def test_start_sets_label_and_tracks(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    monkeypatch.setattr(ms, "_resolve_link",
                        lambda i, t, p: {"repo": "o/r", "issue": "7"})
    added = []
    monkeypatch.setattr(github, "ensure_label", lambda *a, **k: "")
    monkeypatch.setattr(github, "add_issue_label",
                        lambda r, n, label, **k: added.append((r, n, label)))
    ms.start_indicator(str(tmp_path), "fix the bug", "proj")
    assert added == [("o/r", "7", "koan:working")]
    tracker = json.loads(_tracker(tmp_path).read_text())
    assert tracker["fix the bug"]["issue"] == "7"
    assert tracker["fix the bug"]["sha"] is None


def test_start_without_issue_skips_label(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    monkeypatch.setattr(ms, "_resolve_link",
                        lambda i, t, p: {"repo": "o/r", "issue": None})
    called = []
    monkeypatch.setattr(github, "add_issue_label",
                        lambda *a, **k: called.append(a))
    ms.start_indicator(str(tmp_path), "analysis mission", "proj")
    assert called == []
    tracker = json.loads(_tracker(tmp_path).read_text())
    assert tracker["analysis mission"]["issue"] is None


# ---------------------------------------------------------------------------
# on_branch_pushed
# ---------------------------------------------------------------------------


def test_on_branch_pushed_posts_pending(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"fix the bug": {"repo": "o/r", "issue": "7",
                         "sha": None, "project": "proj"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    posted = []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda r, s, st, **k: posted.append((r, s, st)))
    ms.on_branch_pushed(str(tmp_path), "proj", "o/r", "koan/x", "deadbee")
    assert posted == [("o/r", "deadbee", "pending")]
    tracker = json.loads(_tracker(tmp_path).read_text())
    assert tracker["fix the bug"]["sha"] == "deadbee"


def test_on_branch_pushed_no_match_is_noop(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps({}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    posted = []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda *a, **k: posted.append(a))
    ms.on_branch_pushed(str(tmp_path), "proj", "o/r", "koan/x", "sha")
    assert posted == []


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_posts_status_and_removes_label(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"fix the bug": {"repo": "o/r", "issue": "7",
                         "sha": "deadbee", "project": "proj"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    posted, removed = [], []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda r, s, st, **k: posted.append((s, st)))
    monkeypatch.setattr(github, "remove_issue_label",
                        lambda r, n, label, **k: removed.append((n, label)))
    ms.resolve_indicator(str(tmp_path), "fix the bug", success=True)
    assert posted == [("deadbee", "success")]
    assert removed == [("7", "koan:working")]
    assert json.loads(_tracker(tmp_path).read_text()) == {}


def test_resolve_fork_removes_label_from_issue_repo(tmp_path, monkeypatch):
    """Fork workflow: label removed from the issue's repo, status on the fork.

    ``start`` records the upstream issue repo; ``on_branch_pushed`` overwrites
    ``repo`` with the fork push-target. Finalize must remove the label from the
    upstream issue repo, not the fork (which has no such issue).
    """
    import app.github as github
    import app.mission_status as ms

    # Emulate the persisted entry after start + push in a fork workflow:
    # issue_repo = upstream, repo = fork push-target.
    _tracker(tmp_path).write_text(json.dumps(
        {"fix the bug": {"repo": "me/fork", "issue_repo": "upstream/proj",
                         "issue": "7", "sha": "deadbee", "project": "proj"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    posted, removed = [], []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda r, s, st, **k: posted.append((r, s, st)))
    monkeypatch.setattr(github, "remove_issue_label",
                        lambda r, n, label, **k: removed.append((r, n, label)))
    ms.resolve_indicator(str(tmp_path), "fix the bug", success=True)
    assert posted == [("me/fork", "deadbee", "success")]
    assert removed == [("upstream/proj", "7", "koan:working")]


def test_start_then_push_preserves_issue_repo(tmp_path, monkeypatch):
    """A fork push must not clobber the issue repo recorded at start."""
    import app.github as github
    import app.mission_status as ms

    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    monkeypatch.setattr(ms, "_resolve_link",
                        lambda i, t, p: {"repo": "upstream/proj", "issue": "7"})
    monkeypatch.setattr(github, "ensure_label", lambda *a, **k: "")
    monkeypatch.setattr(github, "add_issue_label", lambda *a, **k: None)
    monkeypatch.setattr(github, "set_commit_status", lambda *a, **k: None)
    ms.start_indicator(str(tmp_path), "fix the bug", "proj")
    ms.on_branch_pushed(str(tmp_path), "proj", "me/fork", "koan/x", "deadbee")
    entry = json.loads(_tracker(tmp_path).read_text())["fix the bug"]
    assert entry["issue_repo"] == "upstream/proj"
    assert entry["repo"] == "me/fork"


def test_resolve_failure_posts_red(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"m": {"repo": "o/r", "issue": "7", "sha": "abc", "project": "p"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    posted = []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda r, s, st, **k: posted.append(st))
    monkeypatch.setattr(github, "remove_issue_label", lambda *a, **k: None)
    ms.resolve_indicator(str(tmp_path), "m", success=False)
    assert posted == ["failure"]


def test_resolve_no_sha_skips_status(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"m": {"repo": "o/r", "issue": "7", "sha": None, "project": "p"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    posted = []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda *a, **k: posted.append(a))
    monkeypatch.setattr(github, "remove_issue_label", lambda *a, **k: None)
    ms.resolve_indicator(str(tmp_path), "m", success=True)
    assert posted == []


def test_resolve_unknown_title_is_noop(tmp_path, monkeypatch):
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps({}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    # Must not raise.
    ms.resolve_indicator(str(tmp_path), "absent", success=True)


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def test_reconcile_marks_orphan_as_error(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"orphan": {"repo": "o/r", "issue": "7", "sha": "abc", "project": "p"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    states = []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda r, s, st, **k: states.append(st))
    monkeypatch.setattr(github, "remove_issue_label", lambda *a, **k: None)
    ms.reconcile_stale_indicators(str(tmp_path), active_titles=set())
    assert states == ["error"]
    assert json.loads(_tracker(tmp_path).read_text()) == {}


def test_resolve_keeps_entry_when_commit_status_fails(tmp_path, monkeypatch):
    """A failed commit-status write must keep the entry for reconcile retry and
    still attempt the independent label removal (no orphaned label)."""
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"m": {"repo": "o/r", "issue": "7", "sha": "abc", "project": "p"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    removed = []

    def boom(*a, **k):
        raise RuntimeError("no repo:status scope")

    monkeypatch.setattr(github, "set_commit_status", boom)
    monkeypatch.setattr(github, "remove_issue_label",
                        lambda r, n, label, **k: removed.append((n, label)))
    ms.resolve_indicator(str(tmp_path), "m", success=True)
    # Label still removed despite the commit-status failure...
    assert removed == [("7", "koan:working")]
    # ...and the entry is retained so the next startup reconcile can retry.
    assert "m" in json.loads(_tracker(tmp_path).read_text())


def test_load_backs_up_corrupt_tracker(tmp_path, monkeypatch):
    """Corrupt JSON is moved aside (not clobbered) so entries can be recovered."""
    import app.mission_status as ms

    tracker = _tracker(tmp_path)
    tracker.write_text("{not valid json")
    assert ms._load(str(tmp_path)) == {}
    assert not tracker.exists()
    assert (tmp_path / ".running-indicator.json.corrupt").read_text() == \
        "{not valid json"


def test_resolve_config_fails_closed_on_error(tmp_path, monkeypatch):
    """A projects.yaml read error disables the indicator (fail-closed) instead
    of reverting to the enabled-by-default global config."""
    import app.mission_status as ms

    def boom(_root):
        raise RuntimeError("transient read error")

    monkeypatch.setattr("app.projects_config.load_projects_config", boom)
    cfg = ms._resolve_config("proj")
    assert cfg["enabled"] is False


def test_reconcile_keeps_active(tmp_path, monkeypatch):
    import app.github as github
    import app.mission_status as ms

    _tracker(tmp_path).write_text(json.dumps(
        {"still running": {"repo": "o/r", "issue": "7",
                           "sha": "abc", "project": "p"}}))
    monkeypatch.setattr(ms, "_resolve_config", lambda p: _cfg())
    states = []
    monkeypatch.setattr(github, "set_commit_status",
                        lambda *a, **k: states.append(a))
    ms.reconcile_stale_indicators(str(tmp_path),
                                  active_titles={"still running"})
    assert states == []
    assert "still running" in json.loads(_tracker(tmp_path).read_text())
