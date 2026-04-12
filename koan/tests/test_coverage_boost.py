"""Targeted tests to raise overall coverage past 90%.

Covers: ci_queue, workspace_discovery, focus_manager CLI,
pick_mission CLI, reaction_store, quota_handler CLI.
"""

import json
import os
import runpy
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# ci_queue.py (0% -> ~95%)
# ---------------------------------------------------------------------------


class TestCiQueue:
    def _instance(self, tmp_path):
        d = tmp_path / "instance"
        d.mkdir()
        return str(d)

    def test_enqueue_adds_entry(self, tmp_path):
        from app.ci_queue import enqueue, size, list_entries
        inst = self._instance(tmp_path)
        added = enqueue(inst, "https://gh/pr/1", "feat", "o/r", "1", "/p")
        assert added is True
        assert size(inst) == 1
        entries = list_entries(inst)
        assert entries[0]["pr_url"] == "https://gh/pr/1"

    def test_enqueue_deduplicates(self, tmp_path):
        from app.ci_queue import enqueue, size
        inst = self._instance(tmp_path)
        enqueue(inst, "https://gh/pr/1", "feat", "o/r", "1", "/p")
        added = enqueue(inst, "https://gh/pr/1", "feat2", "o/r", "1", "/p")
        assert added is False
        assert size(inst) == 1

    def test_remove(self, tmp_path):
        from app.ci_queue import enqueue, remove, size
        inst = self._instance(tmp_path)
        enqueue(inst, "https://gh/pr/1", "feat", "o/r", "1", "/p")
        assert remove(inst, "https://gh/pr/1") is True
        assert size(inst) == 0

    def test_remove_nonexistent(self, tmp_path):
        from app.ci_queue import remove
        inst = self._instance(tmp_path)
        assert remove(inst, "https://gh/pr/99") is False

    def test_peek_returns_oldest(self, tmp_path):
        from app.ci_queue import enqueue, peek
        inst = self._instance(tmp_path)
        enqueue(inst, "https://gh/pr/1", "a", "o/r", "1", "/p")
        enqueue(inst, "https://gh/pr/2", "b", "o/r", "2", "/p")
        entry = peek(inst)
        assert entry["pr_url"] == "https://gh/pr/1"

    def test_peek_returns_none_when_empty(self, tmp_path):
        from app.ci_queue import peek
        inst = self._instance(tmp_path)
        assert peek(inst) is None

    def test_expired_entries_are_pruned(self, tmp_path):
        from app.ci_queue import _queue_path, peek, size
        inst = self._instance(tmp_path)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entries = [{"pr_url": "old", "queued_at": old_time}]
        _queue_path(inst).write_text(json.dumps(entries))
        assert peek(inst) is None
        assert size(inst) == 0

    def test_load_handles_corrupt_json(self, tmp_path):
        from app.ci_queue import _load, _queue_path
        inst = self._instance(tmp_path)
        _queue_path(inst).write_text("not json")
        assert _load(inst) == []

    def test_load_handles_non_list(self, tmp_path):
        from app.ci_queue import _load, _queue_path
        inst = self._instance(tmp_path)
        _queue_path(inst).write_text(json.dumps({"not": "list"}))
        assert _load(inst) == []

    def test_is_expired_missing_timestamp(self):
        from app.ci_queue import _is_expired
        assert _is_expired({}) is True

    def test_is_expired_bad_timestamp(self):
        from app.ci_queue import _is_expired
        assert _is_expired({"queued_at": "not-a-date"}) is True

    def test_list_entries_filters_expired(self, tmp_path):
        from app.ci_queue import enqueue, list_entries, _queue_path
        inst = self._instance(tmp_path)
        enqueue(inst, "https://gh/pr/1", "a", "o/r", "1", "/p")
        # Manually add an expired entry
        path = _queue_path(inst)
        entries = json.loads(path.read_text())
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entries.append({"pr_url": "old", "queued_at": old})
        path.write_text(json.dumps(entries))
        valid = list_entries(inst)
        assert len(valid) == 1
        assert valid[0]["pr_url"] == "https://gh/pr/1"


# ---------------------------------------------------------------------------
# workspace_discovery.py (73% -> ~95%)
# ---------------------------------------------------------------------------


class TestWorkspaceDiscovery:
    def test_no_workspace_dir(self, tmp_path):
        from app.workspace_discovery import discover_workspace_projects
        assert discover_workspace_projects(str(tmp_path)) == []

    def test_discovers_projects(self, tmp_path):
        from app.workspace_discovery import discover_workspace_projects
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "project-a").mkdir()
        (ws / "project-b").mkdir()
        results = discover_workspace_projects(str(tmp_path))
        names = [name for name, _ in results]
        assert "project-a" in names and "project-b" in names

    def test_skips_hidden_dirs(self, tmp_path):
        from app.workspace_discovery import discover_workspace_projects
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / ".hidden").mkdir()
        (ws / "visible").mkdir()
        results = discover_workspace_projects(str(tmp_path))
        names = [name for name, _ in results]
        assert "visible" in names
        assert ".hidden" not in names

    def test_skips_files(self, tmp_path):
        from app.workspace_discovery import discover_workspace_projects
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "README.md").write_text("hi")
        (ws / "proj").mkdir()
        results = discover_workspace_projects(str(tmp_path))
        assert len(results) == 1

    def test_handles_os_error_reading_workspace(self, tmp_path):
        from app.workspace_discovery import discover_workspace_projects
        ws = tmp_path / "workspace"
        ws.mkdir()
        with patch("pathlib.Path.iterdir", side_effect=OSError("perm")):
            assert discover_workspace_projects(str(tmp_path)) == []

    def test_handles_broken_symlink(self, tmp_path):
        from app.workspace_discovery import discover_workspace_projects
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "good").mkdir()
        link = ws / "broken"
        link.symlink_to(tmp_path / "nonexistent")
        results = discover_workspace_projects(str(tmp_path))
        names = [name for name, _ in results]
        assert "good" in names


# ---------------------------------------------------------------------------
# focus_manager.py CLI (76% -> ~95%)
# ---------------------------------------------------------------------------


class TestFocusManagerCLI:
    def _run(self, *args, capsys=None):
        argv = ["focus_manager.py", *args]
        exit_code = 0
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(sys, "argv", argv)
                runpy.run_module("app.focus_manager", run_name="__main__")
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 1
        out, err = capsys.readouterr() if capsys else ("", "")
        return exit_code, out, err

    def test_no_args(self, capsys):
        code, _, err = self._run(capsys=capsys)
        assert code == 1 and "Usage:" in err

    def test_check_not_focused(self, tmp_path, capsys):
        code, _, _ = self._run("check", str(tmp_path), capsys=capsys)
        assert code == 1

    def test_check_when_focused(self, tmp_path, capsys):
        from app.focus_manager import create_focus
        create_focus(str(tmp_path), duration=7200, reason="test")
        code, out, _ = self._run("check", str(tmp_path), capsys=capsys)
        assert code == 0
        assert "h" in out.lower() or "m" in out.lower()

    def test_status_not_focused(self, tmp_path, capsys):
        code, out, _ = self._run("status", str(tmp_path), capsys=capsys)
        assert code == 0
        data = json.loads(out.strip())
        assert data["focused"] is False

    def test_status_when_focused(self, tmp_path, capsys):
        from app.focus_manager import create_focus
        create_focus(str(tmp_path), duration=7200, reason="test")
        code, out, _ = self._run("status", str(tmp_path), capsys=capsys)
        data = json.loads(out.strip())
        assert data["focused"] is True

    def test_unknown_command(self, tmp_path, capsys):
        code, _, err = self._run("bogus", str(tmp_path), capsys=capsys)
        assert code == 1 and "Unknown command" in err


# ---------------------------------------------------------------------------
# pick_mission.py CLI (81% -> ~95%)
# ---------------------------------------------------------------------------


class TestPickMissionCLI:
    def _run(self, *args, capsys=None):
        argv = ["pick_mission.py", *args]
        exit_code = 0
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(sys, "argv", argv)
                runpy.run_module("app.pick_mission", run_name="__main__")
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 1
        out, err = capsys.readouterr() if capsys else ("", "")
        return exit_code, out, err

    def test_no_args_prints_usage(self, capsys):
        code, _, err = self._run(capsys=capsys)
        assert code == 1 and "Usage:" in err

    def test_invokes_pick_mission(self, tmp_path, capsys):
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text(
            "## Pending\n\n- [project:foo] do stuff\n\n"
            "## In Progress\n\n## Done\n"
        )
        code, out, _ = self._run(
            str(inst), "foo:/tmp/proj", "1", "IMPLEMENT", "",
            capsys=capsys,
        )
        assert code == 0


# ---------------------------------------------------------------------------
# reaction_store.py (81% -> ~95%)
# ---------------------------------------------------------------------------


class TestReactionStore:
    def test_save_and_load(self, tmp_path):
        from app.reaction_store import save_reaction, load_recent_reactions
        f = tmp_path / "reactions.jsonl"
        save_reaction(f, 1, "👍", True, "hello world", "chat")
        save_reaction(f, 2, "👎", False, "bad msg")
        reactions = load_recent_reactions(f)
        assert len(reactions) == 2
        assert reactions[0]["emoji"] == "👍"
        assert reactions[0]["action"] == "added"
        assert reactions[1]["action"] == "removed"

    def test_load_nonexistent(self, tmp_path):
        from app.reaction_store import load_recent_reactions
        assert load_recent_reactions(tmp_path / "missing.jsonl") == []

    def test_load_corrupt_lines(self, tmp_path):
        from app.reaction_store import load_recent_reactions
        f = tmp_path / "reactions.jsonl"
        f.write_text('{"emoji":"👍"}\nnot json\n{"emoji":"👎"}\n')
        reactions = load_recent_reactions(f)
        assert len(reactions) == 2

    def test_load_max_reactions(self, tmp_path):
        from app.reaction_store import save_reaction, load_recent_reactions
        f = tmp_path / "reactions.jsonl"
        for i in range(10):
            save_reaction(f, i, "👍", True)
        reactions = load_recent_reactions(f, max_reactions=3)
        assert len(reactions) == 3

    def test_save_handles_os_error(self, tmp_path, capsys):
        from app.reaction_store import save_reaction
        f = tmp_path / "reactions.jsonl"
        with patch("builtins.open", side_effect=OSError("nope")):
            save_reaction(f, 1, "👍", True)
        captured = capsys.readouterr()
        assert "Error saving reaction" in captured.out

    def test_load_handles_os_error(self, tmp_path):
        from app.reaction_store import load_recent_reactions
        f = tmp_path / "reactions.jsonl"
        f.write_text('{"emoji":"👍"}\n')
        with patch("builtins.open", side_effect=OSError("nope")):
            assert load_recent_reactions(f) == []

    def test_lookup_message_context_found(self, tmp_path):
        from app.reaction_store import lookup_message_context
        f = tmp_path / "history.jsonl"
        f.write_text(
            json.dumps({"message_id": 42, "text": "hello"}) + "\n"
            + json.dumps({"message_id": 43, "text": "world"}) + "\n"
        )
        result = lookup_message_context(f, 42)
        assert result is not None and result["text"] == "hello"

    def test_lookup_message_context_not_found(self, tmp_path):
        from app.reaction_store import lookup_message_context
        f = tmp_path / "history.jsonl"
        f.write_text(json.dumps({"message_id": 1, "text": "hi"}) + "\n")
        assert lookup_message_context(f, 99) is None

    def test_lookup_message_context_missing_file(self, tmp_path):
        from app.reaction_store import lookup_message_context
        assert lookup_message_context(tmp_path / "missing.jsonl", 1) is None

    def test_lookup_message_context_os_error(self, tmp_path):
        from app.reaction_store import lookup_message_context
        f = tmp_path / "history.jsonl"
        f.write_text('{"message_id":1}\n')
        with patch("builtins.open", side_effect=OSError("nope")):
            assert lookup_message_context(f, 1) is None

    def test_lookup_skips_corrupt_lines(self, tmp_path):
        from app.reaction_store import lookup_message_context
        f = tmp_path / "history.jsonl"
        f.write_text('not json\n{"message_id":42,"text":"ok"}\n')
        assert lookup_message_context(f, 42)["text"] == "ok"

    def test_compact_reactions(self, tmp_path):
        from app.reaction_store import save_reaction, compact_reactions, load_recent_reactions
        f = tmp_path / "reactions.jsonl"
        for i in range(10):
            save_reaction(f, i, "👍", True)
        compact_reactions(f, keep=3)
        assert len(load_recent_reactions(f)) == 3

    def test_compact_nonexistent_noop(self, tmp_path):
        from app.reaction_store import compact_reactions
        compact_reactions(tmp_path / "missing.jsonl")

    def test_compact_empty_noop(self, tmp_path):
        from app.reaction_store import compact_reactions
        f = tmp_path / "reactions.jsonl"
        f.write_text("")
        compact_reactions(f)


# ---------------------------------------------------------------------------
# quota_handler.py CLI (84% -> ~95%)
# ---------------------------------------------------------------------------


class TestQuotaHandlerCLI:
    def _run(self, *args, capsys=None):
        argv = ["quota_handler.py", *args]
        exit_code = 0
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(sys, "argv", argv)
                runpy.run_module("app.quota_handler", run_name="__main__")
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 1
        out, err = capsys.readouterr() if capsys else ("", "")
        return exit_code, out, err

    def test_no_args(self, capsys):
        code, _, err = self._run(capsys=capsys)
        assert code == 1

    def test_not_check_command(self, capsys):
        code, _, err = self._run("status", capsys=capsys)
        assert code == 1

    def test_check_too_few_args(self, capsys):
        code, _, err = self._run("check", "/root", capsys=capsys)
        assert code == 1

    def test_check_no_quota_hit(self, tmp_path, capsys):
        stdout_f = tmp_path / "stdout.txt"
        stderr_f = tmp_path / "stderr.txt"
        stdout_f.write_text("all good\n")
        stderr_f.write_text("")
        code, out, _ = self._run(
            "check", str(tmp_path), str(tmp_path),
            "proj", "5", str(stdout_f), str(stderr_f),
            capsys=capsys,
        )
        assert code == 1  # No quota exhaustion detected

    def test_check_with_non_numeric_run_count(self, tmp_path, capsys):
        stdout_f = tmp_path / "stdout.txt"
        stderr_f = tmp_path / "stderr.txt"
        stdout_f.write_text("ok\n")
        stderr_f.write_text("")
        code, _, _ = self._run(
            "check", str(tmp_path), str(tmp_path),
            "proj", "notnum", str(stdout_f), str(stderr_f),
            capsys=capsys,
        )
        # Should not crash — falls back to run_count=0
        assert code in (0, 1, 2)


