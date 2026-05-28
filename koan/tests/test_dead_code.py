"""Tests for the /dead_code skill — handler and runner."""

import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "dead_code" / "handler.py"


def _load_handler():
    """Load the dead_code handler module dynamically."""
    spec = importlib.util.spec_from_file_location("dead_code_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_path = instance_dir / "missions.md"
    missions_path.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="dead_code",
        args="",
        send_message=MagicMock(),
    )


class TestHandleRouting:
    def test_help_flag_returns_usage(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "Usage:" in result

    def test_help_short_flag_returns_usage(self, handler, ctx):
        ctx.args = "-h"
        result = handler.handle(ctx)
        assert "Usage:" in result

    def test_help_contains_examples(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "/dead_code koan" in result
        assert "/dc" in result

    def test_help_mentions_no_queue(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "--no-queue" in result


class TestHandleQueueMission:
    @patch("app.utils.get_known_projects", return_value=[("myproject", "/path/myproject")])
    @patch("app.utils.insert_pending_mission")
    def test_no_args_uses_first_project(self, mock_insert, mock_projects, handler, ctx):
        ctx.args = ""
        result = handler.handle(ctx)

        assert "Dead code scan queued" in result
        assert "myproject" in result
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][1]
        assert "/dead_code" in mission_entry
        assert "[project:myproject]" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_named_project(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan"
        result = handler.handle(ctx)

        assert "Dead code scan queued" in result
        assert "koan" in result
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_no_queue_flag(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan --no-queue"
        result = handler.handle(ctx)

        assert "Dead code scan queued" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "--no-queue" in mission_entry

    @patch("app.utils.resolve_project_path", return_value=None)
    @patch("app.utils.get_known_projects", return_value=[("web", "/path/web")])
    def test_unknown_project(self, mock_projects, mock_resolve, handler, ctx):
        ctx.args = "nonexistent"
        result = handler.handle(ctx)

        assert "\u274c" in result
        assert "nonexistent" in result
        assert "web" in result

    @patch("app.utils.get_known_projects", return_value=[])
    def test_no_projects_configured(self, mock_projects, handler, ctx):
        ctx.args = ""
        result = handler.handle(ctx)

        assert "\u274c" in result
        assert "No projects" in result

    @patch("app.utils.resolve_project_path", return_value="/path/proj")
    @patch("app.utils.insert_pending_mission")
    def test_no_queue_flag_only(self, mock_insert, mock_resolve, handler, ctx):
        """--no-queue without project name should use default project."""
        ctx.args = "--no-queue"
        # When no project name given, falls through to get_known_projects
        with patch("app.utils.get_known_projects", return_value=[("default", "/path/default")]):
            result = handler.handle(ctx)

        assert "Dead code scan queued" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "--no-queue" in mission_entry
        assert "[project:default]" in mission_entry


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------

from skills.core.dead_code.dead_code_runner import (
    build_dead_code_prompt,
    _prescan_project,
    _prescan_python_references,
    _collect_python_symbols,
    _find_unreferenced_symbols,
    _extract_report_body,
    _extract_dead_code_score,
    _extract_missions,
    _save_report,
    _queue_missions,
    run_dead_code,
    main,
)


class TestPrescanProject:
    def test_detects_python_files(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hi')")
        (tmp_path / "utils.py").write_text("x = 1")

        result = _prescan_project(str(tmp_path))
        assert "Python: 2 files" in result
        assert "main.py" in result
        assert "utils.py" in result

    def test_detects_multiple_languages(self, tmp_path):
        (tmp_path / "app.py").write_text("")
        (tmp_path / "index.js").write_text("")
        (tmp_path / "style.css").write_text("")

        result = _prescan_project(str(tmp_path))
        assert "Python" in result
        assert "JavaScript" in result
        assert "CSS" in result

    def test_skips_venv_and_node_modules(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "lib.py").write_text("")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("")
        (tmp_path / "real.py").write_text("")

        result = _prescan_project(str(tmp_path))
        assert "lib.py" not in result
        assert "pkg.js" not in result
        assert "real.py" in result

    def test_empty_project_returns_empty(self, tmp_path):
        result = _prescan_project(str(tmp_path))
        assert result == ""

    def test_caps_file_listing_at_200(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(250):
            (src / f"mod_{i:03d}.py").write_text("")

        result = _prescan_project(str(tmp_path))
        assert "showing first 200 of 250" in result

    def test_contains_inventory_header(self, tmp_path):
        (tmp_path / "app.py").write_text("")

        result = _prescan_project(str(tmp_path))
        assert "## Pre-scan: Project Inventory" in result
        assert "### Language breakdown" in result
        assert "### Source files" in result


class TestCollectPythonSymbols:
    def test_collects_top_level_functions(self, tmp_path):
        (tmp_path / "mod.py").write_text("def hello():\n    pass\n\ndef world():\n    pass\n")
        defined, contents = _collect_python_symbols(tmp_path)
        assert "hello" in defined
        assert "world" in defined
        assert defined["hello"][0][2] == "function"

    def test_collects_classes(self, tmp_path):
        (tmp_path / "mod.py").write_text("class Foo:\n    pass\n")
        defined, _ = _collect_python_symbols(tmp_path)
        assert "Foo" in defined
        assert defined["Foo"][0][2] == "class"

    def test_skips_private_symbols(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def _private():\n    pass\n"
            "def __dunder__():\n    pass\n"
            "class _Internal:\n    pass\n"
        )
        defined, _ = _collect_python_symbols(tmp_path)
        assert "_private" not in defined
        assert "__dunder__" not in defined
        assert "_Internal" not in defined

    def test_skips_test_functions(self, tmp_path):
        (tmp_path / "mod.py").write_text("def test_something():\n    pass\n")
        defined, _ = _collect_python_symbols(tmp_path)
        assert "test_something" not in defined

    def test_skips_vendored_dirs(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "lib.py").write_text("def vendored():\n    pass\n")
        defined, _ = _collect_python_symbols(tmp_path)
        assert "vendored" not in defined

    def test_handles_syntax_errors(self, tmp_path):
        (tmp_path / "bad.py").write_text("def broken(:\n")
        (tmp_path / "good.py").write_text("def working():\n    pass\n")
        defined, contents = _collect_python_symbols(tmp_path)
        assert "working" in defined
        assert len(contents) == 2  # both files read, only good one parsed

    def test_collects_file_contents(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")
        _, contents = _collect_python_symbols(tmp_path)
        assert len(contents) == 2

    def test_skips_nested_functions(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def outer():\n    def inner():\n        pass\n"
        )
        defined, _ = _collect_python_symbols(tmp_path)
        assert "outer" in defined
        # inner is nested, not top-level — should not be collected
        assert "inner" not in defined

    def test_async_functions(self, tmp_path):
        (tmp_path / "mod.py").write_text("async def fetch():\n    pass\n")
        defined, _ = _collect_python_symbols(tmp_path)
        assert "fetch" in defined
        assert defined["fetch"][0][2] == "function"


class TestFindUnreferencedSymbols:
    def test_finds_unreferenced(self, tmp_path):
        (tmp_path / "a.py").write_text("def used():\n    pass\n\ndef orphan():\n    pass\n")
        (tmp_path / "b.py").write_text("from a import used\nused()\n")
        defined, contents = _collect_python_symbols(tmp_path)
        candidates = _find_unreferenced_symbols(defined, contents)
        names = [c[0] for c in candidates]
        assert "orphan" in names
        assert "used" not in names

    def test_short_names_skipped(self, tmp_path):
        (tmp_path / "a.py").write_text("def ab():\n    pass\n")
        defined, contents = _collect_python_symbols(tmp_path)
        candidates = _find_unreferenced_symbols(defined, contents)
        names = [c[0] for c in candidates]
        assert "ab" not in names

    def test_cross_file_reference_clears(self, tmp_path):
        (tmp_path / "a.py").write_text("def helper():\n    pass\n")
        (tmp_path / "b.py").write_text("# Uses helper somewhere\nresult = helper()\n")
        defined, contents = _collect_python_symbols(tmp_path)
        candidates = _find_unreferenced_symbols(defined, contents)
        names = [c[0] for c in candidates]
        assert "helper" not in names

    def test_empty_project(self, tmp_path):
        defined, contents = _collect_python_symbols(tmp_path)
        candidates = _find_unreferenced_symbols(defined, contents)
        assert candidates == []


class TestPrescanPythonReferences:
    def test_reports_unreferenced_symbols(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def used_func():\n    pass\n\n"
            "def orphan_func():\n    pass\n"
        )
        (tmp_path / "main.py").write_text("from mod import used_func\nused_func()\n")

        result = _prescan_python_references(str(tmp_path))
        assert "orphan_func" in result
        assert "used_func" not in result
        assert "Candidate Dead Code" in result

    def test_empty_project_returns_empty(self, tmp_path):
        result = _prescan_python_references(str(tmp_path))
        assert result == ""

    def test_all_symbols_referenced(self, tmp_path):
        (tmp_path / "a.py").write_text("def func_a():\n    pass\n")
        (tmp_path / "b.py").write_text("from a import func_a\nfunc_a()\n")

        result = _prescan_python_references(str(tmp_path))
        assert result == ""

    def test_includes_verification_note(self, tmp_path):
        (tmp_path / "mod.py").write_text("def lonely():\n    pass\n")

        result = _prescan_python_references(str(tmp_path))
        assert "Verification needed" in result


class TestBuildPromptWithPrescan:
    def test_prompt_includes_inventory_when_path_given(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hi')")

        prompt = build_dead_code_prompt(
            "test",
            project_path=str(tmp_path),
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "dead_code",
        )
        assert "Pre-scan: Project Inventory" in prompt
        assert "Python" in prompt

    def test_prompt_includes_python_refs_when_dead_code_found(self, tmp_path):
        (tmp_path / "mod.py").write_text("def orphan_func():\n    pass\n")
        (tmp_path / "main.py").write_text("x = 1\n")

        prompt = build_dead_code_prompt(
            "test",
            project_path=str(tmp_path),
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "dead_code",
        )
        assert "Candidate Dead Code" in prompt
        assert "orphan_func" in prompt

    def test_prompt_without_path_has_no_inventory(self):
        prompt = build_dead_code_prompt(
            "test",
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "dead_code",
        )
        # The prompt instructions may mention "Pre-scan" but should not
        # contain actual inventory data (language breakdown, source files).
        assert "### Language breakdown" not in prompt
        assert "### Source files" not in prompt


class TestBuildPrompt:
    def test_prompt_contains_project_name(self):
        prompt = build_dead_code_prompt(
            "myproject",
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "dead_code",
        )
        assert "myproject" in prompt

    def test_prompt_contains_instructions(self):
        prompt = build_dead_code_prompt(
            "test",
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "dead_code",
        )
        assert "dead code" in prompt.lower()
        assert "Dead Code Score" in prompt

    def test_prompt_mentions_certainty_levels(self):
        prompt = build_dead_code_prompt(
            "test",
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "dead_code",
        )
        assert "High Certainty" in prompt
        assert "Medium Certainty" in prompt
        assert "Low Certainty" in prompt


class TestExtractReportBody:
    def test_extracts_from_dead_code_header(self):
        raw = "Some preamble\n\nDead Code Report — myproject\n\n## Summary\nClean project."
        result = _extract_report_body(raw)
        assert result.startswith("Dead Code Report")
        assert "## Summary" in result

    def test_extracts_from_summary_header(self):
        raw = "Preamble text\n\n## Summary\nThe project has moderate dead code."
        result = _extract_report_body(raw)
        assert result.startswith("## Summary")

    def test_fallback_to_full_output(self):
        raw = "Just some analysis text with no headers."
        result = _extract_report_body(raw)
        assert result == raw.strip()

    def test_empty_output(self):
        assert _extract_report_body("") == ""

    def test_whitespace_only(self):
        assert _extract_report_body("   \n  ") == ""

    def test_preserves_full_report_after_header(self):
        raw = (
            "Thinking about the code...\n\n"
            "Dead Code Report — proj\n\n"
            "## Summary\nGood.\n\n"
            "## Findings\n\n### High Certainty\n1. Unused import\n"
        )
        result = _extract_report_body(raw)
        assert "## Findings" in result
        assert "Thinking about" not in result


class TestExtractDeadCodeScore:
    def test_valid_score(self):
        report = "**Dead Code Score**: 7/10"
        assert _extract_dead_code_score(report) == 7

    def test_score_1(self):
        assert _extract_dead_code_score("**Dead Code Score**: 1/10") == 1

    def test_score_10(self):
        assert _extract_dead_code_score("**Dead Code Score**: 10/10") == 10

    def test_score_zero_invalid(self):
        assert _extract_dead_code_score("**Dead Code Score**: 0/10") is None

    def test_score_11_invalid(self):
        assert _extract_dead_code_score("**Dead Code Score**: 11/10") is None

    def test_no_score(self):
        assert _extract_dead_code_score("No score here") is None

    def test_score_in_larger_report(self):
        report = (
            "## Summary\n\nSome text.\n\n"
            "**Dead Code Score**: 4/10\n\n"
            "## Findings\n..."
        )
        assert _extract_dead_code_score(report) == 4

    def test_does_not_match_debt_score(self):
        """Should not match tech-debt's Debt Score pattern."""
        report = "**Debt Score**: 5/10"
        assert _extract_dead_code_score(report) is None


class TestExtractMissions:
    def test_extracts_numbered_missions(self):
        report = (
            "## Findings\n\nSome stuff.\n\n"
            "## Suggested Missions\n\n"
            "1. Remove unused imports in utils.py\n"
            "2. Delete dead function parse_legacy()\n"
            "3. Remove commented-out code in auth.py\n"
        )
        missions = _extract_missions(report)
        assert len(missions) == 3
        assert "Remove unused imports" in missions[0]
        assert "Delete dead function" in missions[1]

    def test_extracts_with_dash_suffix(self):
        report = (
            "## Suggested Missions\n\n"
            "1. Remove old_handler — addresses finding #1\n"
            "2. Clean imports — addresses finding #3\n"
        )
        missions = _extract_missions(report)
        assert len(missions) == 2
        assert missions[0] == "Remove old_handler"

    def test_max_five_missions(self):
        lines = "\n".join(f"{i}. Mission {i}" for i in range(1, 8))
        report = f"## Suggested Missions\n\n{lines}\n"
        missions = _extract_missions(report)
        assert len(missions) == 5

    def test_no_section_returns_empty(self):
        report = "## Summary\n\nJust a summary."
        assert _extract_missions(report) == []

    def test_empty_section(self):
        report = "## Suggested Missions\n\n## Next Section\n"
        assert _extract_missions(report) == []

    def test_single_mission(self):
        report = "## Suggested Missions\n\n1. Remove unused import os in main.py\n"
        missions = _extract_missions(report)
        assert len(missions) == 1

    def test_missions_at_end_of_report(self):
        report = (
            "## Summary\nClean.\n\n"
            "## Suggested Missions\n\n"
            "1. Remove dead function\n"
        )
        missions = _extract_missions(report)
        assert len(missions) == 1
        assert "Remove dead function" in missions[0]


class TestSaveReport:
    def test_creates_file_with_header(self, tmp_path):
        report_path = _save_report(tmp_path, "myproject", "## Summary\nClean.", 3)

        assert report_path.exists()
        content = report_path.read_text()
        assert "Last scan:" in content
        assert "Dead code score: 3/10" in content
        assert "## Summary" in content

    def test_creates_directory_structure(self, tmp_path):
        _save_report(tmp_path, "newproject", "Report", None)

        memory_dir = tmp_path / "memory" / "projects" / "newproject"
        assert memory_dir.exists()

    def test_no_score_header_when_none(self, tmp_path):
        _save_report(tmp_path, "proj", "Report", None)

        content = (tmp_path / "memory" / "projects" / "proj" / "dead_code.md").read_text()
        assert "Last scan:" in content
        assert "Dead code score:" not in content

    def test_overwrites_existing_report(self, tmp_path):
        _save_report(tmp_path, "proj", "Old report", 3)
        _save_report(tmp_path, "proj", "New report", 7)

        content = (tmp_path / "memory" / "projects" / "proj" / "dead_code.md").read_text()
        assert "New report" in content
        assert "Old report" not in content

    def test_report_filename(self, tmp_path):
        report_path = _save_report(tmp_path, "proj", "Report", 5)
        assert report_path.name == "dead_code.md"


class TestQueueMissions:
    @patch("app.utils.insert_pending_mission")
    def test_queues_up_to_max(self, mock_insert, tmp_path):
        missions = ["Fix A", "Fix B", "Fix C", "Fix D"]
        queued = _queue_missions(tmp_path, "proj", missions, max_missions=3)

        assert queued == 3
        assert mock_insert.call_count == 3

    @patch("app.utils.insert_pending_mission")
    def test_queue_entry_format(self, mock_insert, tmp_path):
        _queue_missions(tmp_path, "myproj", ["Remove unused import"])

        entry = mock_insert.call_args[0][1]
        assert entry == "- [project:myproj] Remove unused import"

    @patch("app.utils.insert_pending_mission")
    def test_empty_missions(self, mock_insert, tmp_path):
        queued = _queue_missions(tmp_path, "proj", [])
        assert queued == 0
        mock_insert.assert_not_called()

    @patch("app.utils.insert_pending_mission")
    def test_default_max_is_three(self, mock_insert, tmp_path):
        missions = ["A", "B", "C", "D", "E"]
        queued = _queue_missions(tmp_path, "proj", missions)
        assert queued == 3


SAMPLE_REPORT = """\
Dead Code Report — testproj

## Summary

The project has minimal dead code.

**Dead Code Score**: 3/10

## Findings

### High Certainty

1. Unused import `os` in `utils.py:1`
2. Dead function `parse_legacy()` in `parser.py:45` — never called

### Medium Certainty

1. Function `_old_handler()` in `api.py:120` — only referenced in a comment

### Low Certainty

1. Class `LegacySerializer` in `serializers.py:30` — may be used via dynamic import

## Suggested Missions

1. Remove unused imports across the project
2. Delete dead function parse_legacy() and its tests
3. Remove commented-out code blocks in auth.py
"""


class TestRunDeadCode:
    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan", return_value=SAMPLE_REPORT)
    def test_full_pipeline_success(self, mock_scan, mock_prompt, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_path = instance_dir / "missions.md"
        missions_path.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        notify = MagicMock()

        with patch("skills.core.dead_code.dead_code_runner._queue_missions", return_value=3) as mock_queue:
            success, summary = run_dead_code(
                project_path="/path/testproj",
                project_name="testproj",
                instance_dir=str(instance_dir),
                notify_fn=notify,
            )

        assert success
        assert "dead_code.md" in summary
        assert "3/10" in summary
        assert "3 missions queued" in summary
        # Notification calls: scan start + success
        assert notify.call_count == 2

    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan", return_value=SAMPLE_REPORT)
    def test_no_queue_flag(self, mock_scan, mock_prompt, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        with patch("skills.core.dead_code.dead_code_runner._queue_missions") as mock_queue:
            success, summary = run_dead_code(
                project_path="/path/testproj",
                project_name="testproj",
                instance_dir=str(instance_dir),
                notify_fn=notify,
                queue_missions=False,
            )

        assert success
        mock_queue.assert_not_called()

    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan", side_effect=RuntimeError("quota"))
    def test_scan_failure(self, mock_scan, mock_prompt, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        success, summary = run_dead_code(
            project_path="/path/testproj",
            project_name="testproj",
            instance_dir=str(instance_dir),
            notify_fn=notify,
        )

        assert not success
        assert "failed" in summary.lower()

    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan", return_value="")
    def test_empty_output(self, mock_scan, mock_prompt, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        success, summary = run_dead_code(
            project_path="/path/testproj",
            project_name="testproj",
            instance_dir=str(instance_dir),
            notify_fn=notify,
        )

        assert not success
        assert "no output" in summary.lower()

    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan", return_value="Just some analysis with no score.")
    def test_report_without_score(self, mock_scan, mock_prompt, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        with patch("skills.core.dead_code.dead_code_runner._queue_missions", return_value=0):
            success, summary = run_dead_code(
                project_path="/path/testproj",
                project_name="testproj",
                instance_dir=str(instance_dir),
                notify_fn=notify,
            )

        assert success
        assert "score" not in summary

    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan", return_value=SAMPLE_REPORT)
    def test_saves_report_to_memory(self, mock_scan, mock_prompt, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        with patch("skills.core.dead_code.dead_code_runner._queue_missions", return_value=0):
            run_dead_code(
                project_path="/path/testproj",
                project_name="testproj",
                instance_dir=str(instance_dir),
                notify_fn=notify,
            )

        report_path = instance_dir / "memory" / "projects" / "testproj" / "dead_code.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "Dead Code Report" in content

    @patch("skills.core.dead_code.dead_code_runner.build_dead_code_prompt", return_value="scan prompt")
    @patch("skills.core.dead_code.dead_code_runner._run_claude_scan")
    def test_no_missions_found(self, mock_scan, mock_prompt, tmp_path):
        """When no missions section, should succeed without queuing."""
        mock_scan.return_value = (
            "Dead Code Report — proj\n\n## Summary\nVery clean.\n\n"
            "**Dead Code Score**: 1/10\n\n## Findings\n\nNone.\n"
        )
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        success, summary = run_dead_code(
            project_path="/path/proj",
            project_name="proj",
            instance_dir=str(instance_dir),
            notify_fn=notify,
        )

        assert success
        assert "missions queued" not in summary


class TestCLI:
    @patch("skills.core.dead_code.dead_code_runner.run_dead_code", return_value=(True, "Done"))
    def test_main_success(self, mock_run, tmp_path):
        exit_code = main([
            "--project-path", "/path/proj",
            "--project-name", "proj",
            "--instance-dir", str(tmp_path),
        ])
        assert exit_code == 0
        mock_run.assert_called_once()

    @patch("skills.core.dead_code.dead_code_runner.run_dead_code", return_value=(False, "Failed"))
    def test_main_failure(self, mock_run, tmp_path):
        exit_code = main([
            "--project-path", "/path/proj",
            "--project-name", "proj",
            "--instance-dir", str(tmp_path),
        ])
        assert exit_code == 1

    @patch("skills.core.dead_code.dead_code_runner.run_dead_code", return_value=(True, "Done"))
    def test_main_no_queue_flag(self, mock_run, tmp_path):
        main([
            "--project-path", "/path/proj",
            "--project-name", "proj",
            "--instance-dir", str(tmp_path),
            "--no-queue",
        ])
        _, kwargs = mock_run.call_args
        assert kwargs.get("queue_missions") is False

    @patch("skills.core.dead_code.dead_code_runner.run_dead_code", return_value=(True, "Done"))
    def test_main_sets_skill_dir(self, mock_run, tmp_path):
        main([
            "--project-path", "/path/proj",
            "--project-name", "proj",
            "--instance-dir", str(tmp_path),
        ])
        _, kwargs = mock_run.call_args
        skill_dir = kwargs.get("skill_dir")
        assert skill_dir is not None
        assert skill_dir.name == "dead_code"


# ---------------------------------------------------------------------------
# skill_dispatch integration tests
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_dead_code_in_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS
        assert "dead_code" in _SKILL_RUNNERS
        assert _SKILL_RUNNERS["dead_code"] == "skills.core.dead_code.dead_code_runner"

    def test_build_skill_command(self):
        from app.skill_dispatch import build_skill_command

        cmd = build_skill_command(
            command="dead_code",
            args="",
            project_name="myproj",
            project_path="/path/myproj",
            koan_root="/koan",
            instance_dir="/koan/instance",
        )

        assert cmd is not None
        assert "--project-path" in cmd
        assert "/path/myproj" in cmd
        assert "--project-name" in cmd
        assert "myproj" in cmd
        assert "--instance-dir" in cmd

    def test_parse_skill_mission(self):
        from app.skill_dispatch import parse_skill_mission

        project, command, args = parse_skill_mission("/dead_code")
        assert command == "dead_code"
        assert args == ""

    def test_parse_with_project_tag(self):
        from app.skill_dispatch import parse_skill_mission

        project, command, args = parse_skill_mission("[project:koan] /dead_code --no-queue")
        assert project == "koan"
        assert command == "dead_code"
        assert args == "--no-queue"
