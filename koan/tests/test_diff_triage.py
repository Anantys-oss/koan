"""Tests for content-aware diff triage (diff_triage.py)."""

import pytest

from app.diff_triage import (
    TriagedFile,
    _extract_changed_lines,
    _is_rename_only,
    _is_whitespace_only,
    triage_diff_files,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_file_diff(path, added_lines=None, removed_lines=None, rename=False):
    """Build a minimal unified diff block for a single file."""
    header = f"diff --git a/{path} b/{path}\n"
    header += "index abc1234..def5678 100644\n"
    header += f"--- a/{path}\n"
    header += f"+++ b/{path}\n"

    if rename:
        header = (
            f"diff --git a/{path} b/{path}\n"
            f"similarity index 100%\n"
            f"rename from old/{path}\n"
            f"rename to {path}\n"
        )
        return header

    hunk = "@@ -1,5 +1,5 @@\n"
    hunk += " context line\n"
    for line in (removed_lines or []):
        hunk += f"-{line}\n"
    for line in (added_lines or []):
        hunk += f"+{line}\n"
    hunk += " more context\n"

    return header + hunk


def _combine_diffs(*blocks):
    return "\n".join(blocks)


_ENABLED_CONFIG = {
    "enabled": True,
    "skip_lockfiles": True,
    "skip_generated": True,
    "skip_whitespace_only": True,
    "skip_renames": True,
}


# ── triage_diff_files basic behavior ────────────────────────────────────


class TestTriageDiffFiles:

    def test_disabled_returns_diff_unchanged(self):
        diff = _make_file_diff("package-lock.json", ["new"], ["old"])
        result, triaged = triage_diff_files(diff, {"enabled": False})
        assert result == diff
        assert triaged == []

    def test_empty_diff_returns_empty(self):
        result, triaged = triage_diff_files("", _ENABLED_CONFIG)
        assert result == ""
        assert triaged == []

    def test_no_trivial_files_keeps_all(self):
        diff = _make_file_diff("src/main.py", ["x = 1"], ["x = 2"])
        result, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert "src/main.py" in result
        assert triaged == []

    def test_single_block_diff_returned_unchanged(self):
        diff = "not a real diff format"
        result, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert result == diff
        assert triaged == []


# ── Lockfile triage ─────────────────────────────────────────────────────


class TestLockfileTriage:

    def test_package_lock_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("src/app.py", ["new_code()"], ["old_code()"]),
            _make_file_diff("package-lock.json", ['"version": "2.0"'], ['"version": "1.0"']),
        )
        result, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert "package-lock.json" not in result
        assert "src/app.py" in result
        assert len(triaged) == 1
        assert triaged[0].path == "package-lock.json"
        assert triaged[0].reason == "lockfile"

    def test_yarn_lock_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("index.js", ["a"], ["b"]),
            _make_file_diff("yarn.lock", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "yarn.lock" for t in triaged)

    def test_poetry_lock_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            _make_file_diff("poetry.lock", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "poetry.lock" for t in triaged)

    def test_cargo_lock_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("main.rs", ["a"], ["b"]),
            _make_file_diff("Cargo.lock", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "Cargo.lock" for t in triaged)

    def test_lockfile_skip_disabled(self):
        config = {**_ENABLED_CONFIG, "skip_lockfiles": False}
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            _make_file_diff("package-lock.json", ["x"], ["y"]),
        )
        result, triaged = triage_diff_files(diff, config)
        assert "package-lock.json" in result
        assert not any(t.path == "package-lock.json" for t in triaged)


# ── Generated file triage ───────────────────────────────────────────────


class TestGeneratedFileTriage:

    def test_minified_js_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("src/app.js", ["a"], ["b"]),
            _make_file_diff("dist/bundle.min.js", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "dist/bundle.min.js" and t.reason == "generated" for t in triaged)

    def test_sourcemap_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("src/app.ts", ["a"], ["b"]),
            _make_file_diff("dist/app.js.map", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "dist/app.js.map" for t in triaged)

    def test_protobuf_go_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("main.go", ["a"], ["b"]),
            _make_file_diff("proto/service.pb.go", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "proto/service.pb.go" for t in triaged)

    def test_protobuf_python_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            _make_file_diff("proto/service_pb2.py", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "proto/service_pb2.py" for t in triaged)

    def test_snapshot_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("Component.tsx", ["a"], ["b"]),
            _make_file_diff("__tests__/Component.test.tsx.snap", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.reason == "generated" for t in triaged)

    def test_generated_skip_disabled(self):
        config = {**_ENABLED_CONFIG, "skip_generated": False}
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            _make_file_diff("dist/bundle.min.js", ["x"], ["y"]),
        )
        result, triaged = triage_diff_files(diff, config)
        assert "bundle.min.js" in result


# ── Whitespace-only triage ──────────────────────────────────────────────


class TestWhitespaceOnlyTriage:

    def test_indentation_change_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("main.py", ["real_change()"], ["old_code()"]),
            _make_file_diff("utils.py", ["    indented"], ["  indented"]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "utils.py" and t.reason == "whitespace-only" for t in triaged)

    def test_blank_line_changes_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("main.py", ["code()"], ["old()"]),
            _make_file_diff("config.py", ["", ""], [""]),
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.path == "config.py" and t.reason == "whitespace-only" for t in triaged)

    def test_real_content_change_kept(self):
        diff = _make_file_diff("main.py", ["new_function()"], ["old_function()"])
        result, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert "main.py" in result
        assert not any(t.path == "main.py" for t in triaged)

    def test_whitespace_skip_disabled(self):
        config = {**_ENABLED_CONFIG, "skip_whitespace_only": False}
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            _make_file_diff("utils.py", ["    x"], ["  x"]),
        )
        result, triaged = triage_diff_files(diff, config)
        assert "utils.py" in result


# ── Rename-only triage ──────────────────────────────────────────────────


class TestRenameOnlyTriage:

    def test_pure_rename_skipped(self):
        rename_block = (
            "diff --git a/old/name.py b/new/name.py\n"
            "similarity index 100%\n"
            "rename from old/name.py\n"
            "rename to new/name.py\n"
        )
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            rename_block,
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert any(t.reason == "rename-only" for t in triaged)

    def test_rename_with_changes_kept(self):
        rename_with_change = (
            "diff --git a/old/name.py b/new/name.py\n"
            "similarity index 85%\n"
            "rename from old/name.py\n"
            "rename to new/name.py\n"
            "index abc..def 100644\n"
            "--- a/old/name.py\n"
            "+++ b/new/name.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-old_code()\n"
            "+new_code()\n"
        )
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            rename_with_change,
        )
        _, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert not any(t.reason == "rename-only" for t in triaged)

    def test_rename_skip_disabled(self):
        config = {**_ENABLED_CONFIG, "skip_renames": False}
        rename_block = (
            "diff --git a/old/name.py b/new/name.py\n"
            "similarity index 100%\n"
            "rename from old/name.py\n"
            "rename to new/name.py\n"
        )
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            rename_block,
        )
        result, triaged = triage_diff_files(diff, config)
        assert not any(t.reason == "rename-only" for t in triaged)


# ── Multiple trivial files ──────────────────────────────────────────────


class TestMultipleTriagedFiles:

    def test_multiple_trivial_files_all_skipped(self):
        diff = _combine_diffs(
            _make_file_diff("src/core.py", ["important()"], ["old()"]),
            _make_file_diff("package-lock.json", ["v2"], ["v1"]),
            _make_file_diff("dist/app.min.js", ["minified"], ["old_minified"]),
            _make_file_diff("style.css", ["    margin: 0"], ["  margin: 0"]),
        )
        result, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert "src/core.py" in result
        assert "package-lock.json" not in result
        assert "app.min.js" not in result
        assert len(triaged) >= 2  # lockfile + generated; whitespace depends on detection

    def test_all_files_trivial_returns_empty_diff(self):
        diff = _combine_diffs(
            _make_file_diff("package-lock.json", ["v2"], ["v1"]),
            _make_file_diff("yarn.lock", ["x"], ["y"]),
        )
        result, triaged = triage_diff_files(diff, _ENABLED_CONFIG)
        assert len(triaged) == 2
        assert "package-lock.json" not in result
        assert "yarn.lock" not in result


# ── Helper function tests ───────────────────────────────────────────────


class TestExtractChangedLines:

    def test_basic_extraction(self):
        text = "@@ -1,3 +1,3 @@\n context\n-old\n+new\n context\n"
        added, removed = _extract_changed_lines(text)
        assert added == ["new"]
        assert removed == ["old"]

    def test_multiple_hunks(self):
        text = (
            "@@ -1,2 +1,2 @@\n-a\n+b\n"
            "@@ -10,2 +10,2 @@\n-c\n+d\n"
        )
        added, removed = _extract_changed_lines(text)
        assert added == ["b", "d"]
        assert removed == ["a", "c"]

    def test_only_additions(self):
        text = "@@ -1,1 +1,3 @@\n context\n+new1\n+new2\n"
        added, removed = _extract_changed_lines(text)
        assert added == ["new1", "new2"]
        assert removed == []

    def test_empty_hunks(self):
        added, removed = _extract_changed_lines("")
        assert added == []
        assert removed == []


class TestIsWhitespaceOnly:

    def test_identical_stripped_content(self):
        assert _is_whitespace_only(["  hello  "], ["hello"])

    def test_blank_lines_only(self):
        assert _is_whitespace_only(["", " "], [""])

    def test_real_content_change(self):
        assert not _is_whitespace_only(["new_func()"], ["old_func()"])

    def test_empty_changes(self):
        assert _is_whitespace_only([], [])


class TestIsRenameOnly:

    def test_rename_without_hunks(self):
        block = (
            "diff --git a/old.py b/new.py\n"
            "rename from old.py\n"
            "rename to new.py\n"
        )
        assert _is_rename_only(block)

    def test_rename_with_hunks(self):
        block = (
            "diff --git a/old.py b/new.py\n"
            "rename from old.py\n"
            "rename to new.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
        )
        assert not _is_rename_only(block)

    def test_not_a_rename(self):
        block = (
            "diff --git a/foo.py b/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
        )
        assert not _is_rename_only(block)


# ── Config integration ──────────────────────────────────────────────────


class TestConfigIntegration:

    def test_all_flags_disabled_keeps_everything(self):
        config = {
            "enabled": True,
            "skip_lockfiles": False,
            "skip_generated": False,
            "skip_whitespace_only": False,
            "skip_renames": False,
        }
        diff = _combine_diffs(
            _make_file_diff("package-lock.json", ["v2"], ["v1"]),
            _make_file_diff("dist/app.min.js", ["x"], ["y"]),
        )
        result, triaged = triage_diff_files(diff, config)
        assert triaged == []
        assert "package-lock.json" in result
        assert "app.min.js" in result

    def test_missing_config_keys_use_defaults(self):
        config = {"enabled": True}
        diff = _combine_diffs(
            _make_file_diff("main.py", ["a"], ["b"]),
            _make_file_diff("package-lock.json", ["x"], ["y"]),
        )
        _, triaged = triage_diff_files(diff, config)
        assert any(t.path == "package-lock.json" for t in triaged)
