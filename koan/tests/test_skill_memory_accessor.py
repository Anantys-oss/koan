"""Tests for MemoryAccessor — lazy memory wrapper for SkillContext."""

from unittest.mock import MagicMock, patch

import pytest

from app.skill_memory_accessor import MemoryAccessor


@pytest.fixture
def instance_dir(tmp_path):
    mem = tmp_path / "memory" / "projects" / "test-proj"
    mem.mkdir(parents=True)
    return tmp_path


class TestLazyInit:
    def test_no_manager_created_until_write_or_search(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        assert acc._manager is None

    def test_manager_created_on_append(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        with patch("app.memory_manager.MemoryManager") as mock_mm:
            acc.append("observation", "test content", project="test-proj")
            mock_mm.assert_called_once_with(str(instance_dir))


class TestReadLearnings:
    def test_returns_filtered_learnings(self, instance_dir):
        learnings = instance_dir / "memory" / "projects" / "test-proj" / "learnings.md"
        learnings.write_text("- Python 3.11 required\n- Use ruff for linting\n")
        acc = MemoryAccessor(instance_dir)
        result = acc.read_learnings("test-proj", task_text="linting setup")
        assert isinstance(result, str)

    def test_returns_empty_for_missing_project(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        result = acc.read_learnings("nonexistent")
        assert result == ""

    def test_returns_empty_for_blank_project(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        assert acc.read_learnings("") == ""


class TestReadContext:
    def test_returns_context_content(self, instance_dir):
        ctx_file = instance_dir / "memory" / "projects" / "test-proj" / "context.md"
        ctx_file.write_text("Architecture: monolith\n")
        acc = MemoryAccessor(instance_dir)
        result = acc.read_context("test-proj")
        assert "Architecture: monolith" in result

    def test_returns_empty_for_missing(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        assert acc.read_context("nonexistent") == ""

    def test_returns_empty_for_blank_project(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        assert acc.read_context("") == ""


class TestReadBlock:
    def test_returns_empty_for_blank_project(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        assert acc.read_block("") == ""

    def test_delegates_to_build_memory_block(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        with patch("app.skill_memory.build_memory_block") as mock_build:
            mock_build.return_value = "<memory-context>block</memory-context>"
            result = acc.read_block("test-proj", "task text")
            mock_build.assert_called_once_with(
                str(instance_dir), "test-proj", "task text", title="Project Memory",
            )
            assert result == "<memory-context>block</memory-context>"


class TestAppend:
    def test_delegates_to_memory_manager(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        with patch("app.memory_manager.MemoryManager") as mock_cls:
            mock_mgr = MagicMock()
            mock_cls.return_value = mock_mgr
            acc.append("observation", "learned something", project="test-proj")
            mock_mgr.append_memory_entry.assert_called_once_with(
                "observation", "test-proj", "learned something",
            )

    def test_empty_project_passes_none(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        with patch("app.memory_manager.MemoryManager") as mock_cls:
            mock_mgr = MagicMock()
            mock_cls.return_value = mock_mgr
            acc.append("observation", "global note")
            mock_mgr.append_memory_entry.assert_called_once_with(
                "observation", None, "global note",
            )


class TestSearch:
    def test_delegates_to_read_memory_window(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        with patch("app.memory_manager.MemoryManager") as mock_cls:
            mock_mgr = MagicMock()
            mock_mgr.read_memory_window.return_value = [{"content": "hit"}]
            mock_cls.return_value = mock_mgr
            results = acc.search("test query", project="test-proj", max_results=5)
            mock_mgr.read_memory_window.assert_called_once_with(
                "test-proj", max_entries=5, query_text="test query",
            )
            assert results == [{"content": "hit"}]

    def test_empty_project_passes_none(self, instance_dir):
        acc = MemoryAccessor(instance_dir)
        with patch("app.memory_manager.MemoryManager") as mock_cls:
            mock_mgr = MagicMock()
            mock_mgr.read_memory_window.return_value = []
            mock_cls.return_value = mock_mgr
            acc.search("query")
            mock_mgr.read_memory_window.assert_called_once_with(
                None, max_entries=10, query_text="query",
            )
