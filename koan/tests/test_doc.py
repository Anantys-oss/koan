"""Tests for the /doc skill — handler, runner, and block parsing."""

import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "doc" / "handler.py"


def _load_handler():
    """Load the doc handler module dynamically."""
    spec = importlib.util.spec_from_file_location("doc_handler", str(HANDLER_PATH))
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
        command_name="doc",
        args="",
        send_message=MagicMock(),
    )


class TestHandleRouting:
    def test_help_flag_returns_usage(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "Usage:" in result

    def test_help_short_flag(self, handler, ctx):
        ctx.args = "-h"
        result = handler.handle(ctx)
        assert "Usage:" in result

    def test_no_args_returns_error(self, handler, ctx):
        ctx.args = ""
        result = handler.handle(ctx)
        assert "\u274c" in result


class TestHandleQueueMission:
    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_named_project(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan"
        result = handler.handle(ctx)

        assert "Documentation extraction queued" in result
        assert "koan" in result
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][0]
        assert mock_insert.call_args[0][1] == "koan"
        assert "/doc" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_with_categories(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan architecture,test-style"
        result = handler.handle(ctx)

        assert "Documentation extraction queued" in result
        assert "architecture,test-style" in result
        mission_entry = mock_insert.call_args[0][0]
        assert "architecture,test-style" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_mode_flag(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan --mode=update"
        result = handler.handle(ctx)

        assert "Documentation extraction queued" in result
        assert "mode: update" in result
        mission_entry = mock_insert.call_args[0][0]
        assert "--mode=update" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_mode_flag_with_categories(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan architecture --mode=replace"
        result = handler.handle(ctx)

        mission_entry = mock_insert.call_args[0][0]
        assert "--mode=replace" in mission_entry
        assert "architecture" in mission_entry

    @patch("app.utils.resolve_project_name_and_path", return_value=("backend", "/path/backend"))
    @patch("app.utils.insert_pending_mission")
    def test_alias_resolves_to_canonical(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "be"
        result = handler.handle(ctx)

        assert "Documentation extraction queued" in result
        assert "backend" in result
        assert mock_insert.call_args[0][1] == "backend"

    @patch("app.utils.resolve_project_path", return_value=None)
    @patch("app.utils.get_known_projects", return_value=[("web", "/path/web")])
    def test_unknown_project(self, mock_projects, mock_resolve, handler, ctx):
        ctx.args = "nonexistent"
        result = handler.handle(ctx)

        assert "\u274c" in result
        assert "nonexistent" in result
        assert "web" in result


# ---------------------------------------------------------------------------
# Runner tests — block parsing
# ---------------------------------------------------------------------------

from skills.core.doc.doc_runner import (
    parse_doc_blocks,
    DocBlock,
    merge_doc,
    write_doc_file,
    _split_sections,
    _describe_existing_docs,
    build_doc_prompt,
    ALL_CATEGORIES,
    run_doc,
    main,
)


class TestParseDocBlocks:
    def test_single_block(self):
        raw = (
            "Some preamble text\n\n"
            "---DOC---\n"
            "category: architecture\n"
            "title: Architecture Overview\n"
            "---\n"
            "## Module Map\n\nMain entry point is run.py.\n"
            "---END DOC---\n"
            "\nSome trailing text"
        )
        blocks = parse_doc_blocks(raw)
        assert len(blocks) == 1
        assert blocks[0].category == "architecture"
        assert blocks[0].title == "Architecture Overview"
        assert "Module Map" in blocks[0].content
        assert blocks[0].filename == "architecture.md"

    def test_multiple_blocks(self):
        raw = (
            "---DOC---\n"
            "category: architecture\n"
            "title: Arch\n"
            "---\n"
            "Content A\n"
            "---END DOC---\n"
            "\n"
            "---DOC---\n"
            "category: code-style\n"
            "title: Code Style Guide\n"
            "---\n"
            "Content B\n"
            "---END DOC---\n"
        )
        blocks = parse_doc_blocks(raw)
        assert len(blocks) == 2
        assert blocks[0].category == "architecture"
        assert blocks[1].category == "code-style"
        assert blocks[1].filename == "code-style.md"

    def test_no_blocks(self):
        raw = "Just some plain text without any blocks."
        blocks = parse_doc_blocks(raw)
        assert blocks == []

    def test_empty_content(self):
        raw = (
            "---DOC---\n"
            "category: modules\n"
            "title: Modules\n"
            "---\n"
            "---END DOC---\n"
        )
        blocks = parse_doc_blocks(raw)
        assert len(blocks) == 1
        assert blocks[0].content == ""


class TestSplitSections:
    def test_no_headings(self):
        text = "Just plain text."
        result = _split_sections(text)
        assert result == {"__preamble__": text}

    def test_single_heading(self):
        text = "## Overview\n\nSome content here."
        result = _split_sections(text)
        assert "## Overview" in result
        assert "__preamble__" not in result

    def test_multiple_headings(self):
        text = "# Title\n\n## First\n\nContent 1\n\n## Second\n\nContent 2"
        result = _split_sections(text)
        assert "__preamble__" in result
        assert "## First" in result
        assert "## Second" in result

    def test_preamble_before_heading(self):
        text = "Intro text\n\n## Section\n\nBody"
        result = _split_sections(text)
        assert result["__preamble__"] == "Intro text"


class TestMergeDoc:
    def test_new_section_appended(self):
        existing = "## Overview\n\nOld overview content."
        new = "## Overview\n\nNew overview content.\n\n## Testing\n\nTest patterns."
        result = merge_doc(existing, new)
        assert "New overview content." in result
        assert "Test patterns." in result
        assert "Old overview content" not in result

    def test_existing_section_preserved_when_not_in_new(self):
        existing = "## Overview\n\nContent.\n\n## Legacy\n\nLegacy stuff."
        new = "## Overview\n\nUpdated."
        result = merge_doc(existing, new)
        assert "Updated." in result
        assert "Legacy stuff." in result

    def test_preamble_from_new_preferred(self):
        existing = "Old preamble\n\n## Section\n\nContent."
        new = "New preamble\n\n## Section\n\nContent."
        result = merge_doc(existing, new)
        assert "New preamble" in result
        assert "Old preamble" not in result


class TestWriteDocFile:
    def test_create_mode_writes_new(self, tmp_path):
        block = DocBlock("architecture", "Architecture", "Content here")
        path = write_doc_file(tmp_path, block, "create")
        assert path is not None
        assert path.read_text().startswith("# Architecture")
        assert "Content here" in path.read_text()

    def test_create_mode_skips_existing(self, tmp_path):
        (tmp_path / "architecture.md").write_text("Existing")
        block = DocBlock("architecture", "Architecture", "New content")
        path = write_doc_file(tmp_path, block, "create")
        assert path is None
        assert (tmp_path / "architecture.md").read_text() == "Existing"

    def test_replace_mode_overwrites(self, tmp_path):
        (tmp_path / "architecture.md").write_text("Old content")
        block = DocBlock("architecture", "Architecture", "New content")
        path = write_doc_file(tmp_path, block, "replace")
        assert path is not None
        assert "New content" in path.read_text()
        assert "Old content" not in path.read_text()

    def test_update_mode_merges(self, tmp_path):
        (tmp_path / "code-style.md").write_text(
            "# Code Style\n\n## Naming\n\nOld naming.\n\n## Imports\n\nOld imports."
        )
        block = DocBlock(
            "code-style", "Code Style",
            "## Naming\n\nNew naming.\n\n## Formatting\n\nNew formatting.",
        )
        path = write_doc_file(tmp_path, block, "update")
        assert path is not None
        content = path.read_text()
        assert "New naming." in content
        assert "Old imports." in content
        assert "New formatting." in content
        assert "Old naming." not in content

    def test_update_mode_creates_when_missing(self, tmp_path):
        block = DocBlock("modules", "Modules", "Content")
        path = write_doc_file(tmp_path, block, "update")
        assert path is not None
        assert "Content" in path.read_text()


class TestDescribeExistingDocs:
    def test_no_docs_dir(self, tmp_path):
        result = _describe_existing_docs(tmp_path / "docs", ["architecture"])
        assert "No docs/ directory" in result

    def test_existing_and_missing(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "architecture.md").write_text("# Arch\n\nContent\n")
        result = _describe_existing_docs(docs, ["architecture", "code-style"])
        assert "already exists" in result
        assert "does not exist" in result


class TestBuildPrompt:
    def test_prompt_contains_project_name(self):
        prompt = build_doc_prompt(
            "myproject", ["architecture"], "create", "No docs yet.",
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "doc",
        )
        assert "myproject" in prompt

    def test_prompt_contains_categories(self):
        prompt = build_doc_prompt(
            "test", ["architecture", "code-style"], "update", "",
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "doc",
        )
        assert "architecture" in prompt
        assert "code-style" in prompt


class TestRunDoc:
    @patch("skills.core.doc.doc_runner._run_claude_scan")
    def test_success_writes_files(self, mock_scan, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_scan.return_value = (
            "---DOC---\n"
            "category: architecture\n"
            "title: Architecture\n"
            "---\n"
            "## Overview\n\nProject overview.\n"
            "---END DOC---\n"
        )

        success, summary = run_doc(
            project_path=str(project_dir),
            project_name="test",
            instance_dir=str(instance_dir),
            categories=["architecture"],
            notify_fn=MagicMock(),
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "doc",
        )

        assert success is True
        assert "1 files written" in summary
        assert (project_dir / "docs" / "architecture.md").exists()
        content = (project_dir / "docs" / "architecture.md").read_text()
        assert "Project overview." in content

    @patch("skills.core.doc.doc_runner._run_claude_scan")
    def test_no_blocks_returns_failure(self, mock_scan, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_scan.return_value = "Just plain text without blocks."

        success, summary = run_doc(
            project_path=str(project_dir),
            project_name="test",
            instance_dir=str(instance_dir),
            notify_fn=MagicMock(),
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "doc",
        )

        assert success is False
        assert "No ---DOC--- blocks" in summary

    @patch("skills.core.doc.doc_runner._run_claude_scan")
    def test_empty_output_returns_failure(self, mock_scan, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_scan.return_value = ""

        success, summary = run_doc(
            project_path=str(project_dir),
            project_name="test",
            instance_dir=str(instance_dir),
            notify_fn=MagicMock(),
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "doc",
        )

        assert success is False

    def test_invalid_categories_returns_failure(self, tmp_path):
        success, summary = run_doc(
            project_path=str(tmp_path),
            project_name="test",
            instance_dir=str(tmp_path),
            categories=["invalid-cat"],
            notify_fn=MagicMock(),
        )
        assert success is False
        assert "Unknown categories" in summary

    @patch("skills.core.doc.doc_runner._run_claude_scan")
    def test_create_mode_skips_existing(self, mock_scan, tmp_path):
        project_dir = tmp_path / "project"
        docs_dir = project_dir / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "architecture.md").write_text("Existing content")
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        mock_scan.return_value = (
            "---DOC---\n"
            "category: architecture\n"
            "title: Architecture\n"
            "---\n"
            "New content\n"
            "---END DOC---\n"
        )

        success, summary = run_doc(
            project_path=str(project_dir),
            project_name="test",
            instance_dir=str(instance_dir),
            categories=["architecture"],
            mode="create",
            notify_fn=MagicMock(),
            skill_dir=Path(__file__).parent.parent / "skills" / "core" / "doc",
        )

        assert success is True
        assert "1 skipped" in summary
        assert (docs_dir / "architecture.md").read_text() == "Existing content"


class TestMainCLI:
    @patch("skills.core.doc.doc_runner.run_doc")
    def test_main_parses_args(self, mock_run):
        mock_run.return_value = (True, "Done")
        code = main([
            "--project-path", "/tmp/proj",
            "--project-name", "test",
            "--instance-dir", "/tmp/inst",
            "--categories", "architecture,code-style",
            "--mode", "update",
        ])
        assert code == 0
        mock_run.assert_called_once()
        kwargs = mock_run.call_args
        assert kwargs[1]["categories"] == ["architecture", "code-style"]
        assert kwargs[1]["mode"] == "update"

    @patch("skills.core.doc.doc_runner.run_doc")
    def test_main_default_categories(self, mock_run):
        mock_run.return_value = (True, "Done")
        main([
            "--project-path", "/tmp/proj",
            "--project-name", "test",
            "--instance-dir", "/tmp/inst",
        ])
        kwargs = mock_run.call_args
        assert kwargs[1]["categories"] is None

    @patch("skills.core.doc.doc_runner.run_doc")
    def test_main_failure_returns_1(self, mock_run):
        mock_run.return_value = (False, "Failed")
        code = main([
            "--project-path", "/tmp/proj",
            "--project-name", "test",
            "--instance-dir", "/tmp/inst",
        ])
        assert code == 1
