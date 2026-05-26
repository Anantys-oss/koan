"""Tests for the /diagnose skill handler."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


HANDLER_PATH = (
    Path(__file__).parent.parent / "skills" / "core" / "diagnose" / "handler.py"
)

MISSIONS_SKELETON = (
    "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n"
)


def _load_handler():
    spec = importlib.util.spec_from_file_location("diagnose_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_path = instance_dir / "missions.md"
    missions_path.write_text(MISSIONS_SKELETON)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="diagnose",
        args="",
        send_message=MagicMock(),
    )


class TestNoArgs:
    def test_returns_no_failures_when_failed_section_empty(self, handler, ctx):
        result = handler.handle(ctx)
        assert "No failed missions" in result

    def test_returns_no_failures_when_no_missions_file(self, handler, ctx):
        (ctx.instance_dir / "missions.md").unlink()
        result = handler.handle(ctx)
        assert "No missions.md" in result


class TestFindLastFailure:
    def test_finds_single_failure(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Fix the build ❌ (2026-05-20 14:30)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(ctx.instance_dir / "missions.md")
        assert result is not None
        assert "Fix the build" in result["text"]
        assert result["date"] == "2026-05-20"
        assert result["time"] == "14:30"
        assert result["cause_tag"] is None

    def test_finds_most_recent_among_multiple(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Old failure ❌ (2026-05-18 10:00)\n"
            "- Recent failure ❌ (2026-05-20 16:00)\n"
            "- Middle failure ❌ (2026-05-19 12:00)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(ctx.instance_dir / "missions.md")
        assert "Recent failure" in result["text"]

    def test_extracts_cause_tag(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Stuck mission ❌ (2026-05-20 14:30) [stagnation:tool_loop]\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(ctx.instance_dir / "missions.md")
        assert result["cause_tag"] == "stagnation:tool_loop"

    def test_extracts_project_tag(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- [project:myapp] Deploy fix ❌ (2026-05-20 14:30)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(ctx.instance_dir / "missions.md")
        assert result["project"] == "myapp"
        assert "Deploy fix" in result["text"]

    def test_filters_by_project(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- [project:web] Web failure ❌ (2026-05-20 16:00)\n"
            "- [project:api] API failure ❌ (2026-05-20 14:00)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(
            ctx.instance_dir / "missions.md", project_filter="api",
        )
        assert "API failure" in result["text"]

    def test_filter_returns_none_when_no_match(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- [project:web] Web failure ❌ (2026-05-20 16:00)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(
            ctx.instance_dir / "missions.md", project_filter="api",
        )
        assert result is None

    def test_skips_entries_without_timestamp(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Malformed entry without timestamp\n"
            "- Good entry ❌ (2026-05-20 14:30)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler._find_last_failure(ctx.instance_dir / "missions.md")
        assert result is not None
        assert "Good entry" in result["text"]


class TestJournalContext:
    def test_reads_project_journal(self, handler, ctx):
        journal_dir = ctx.instance_dir / "journal" / "2026-05-20"
        journal_dir.mkdir(parents=True)
        (journal_dir / "myapp.md").write_text("## Session\nTests failed on auth module")

        result = handler._get_journal_context(
            ctx.instance_dir, "myapp", "2026-05-20",
        )
        assert "Tests failed on auth module" in result

    def test_falls_back_to_all_journals(self, handler, ctx):
        journal_dir = ctx.instance_dir / "journal" / "2026-05-20"
        journal_dir.mkdir(parents=True)
        (journal_dir / "other.md").write_text("Something happened")

        result = handler._get_journal_context(
            ctx.instance_dir, "myapp", "2026-05-20",
        )
        assert "Something happened" in result

    def test_returns_none_when_no_journal(self, handler, ctx):
        result = handler._get_journal_context(
            ctx.instance_dir, "myapp", "2026-05-20",
        )
        assert result is None

    def test_truncates_long_journal(self, handler, ctx):
        journal_dir = ctx.instance_dir / "journal" / "2026-05-20"
        journal_dir.mkdir(parents=True)
        (journal_dir / "myapp.md").write_text("x" * 5000)

        result = handler._get_journal_context(
            ctx.instance_dir, "myapp", "2026-05-20",
        )
        assert len(result) <= handler._MAX_JOURNAL_CHARS
        assert result.startswith("...")


class TestQueueFixMission:
    def test_queues_mission_with_context(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- [project:myapp] Fix auth bug ❌ (2026-05-20 14:30) [stagnation:tool_loop]\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        journal_dir = ctx.instance_dir / "journal" / "2026-05-20"
        journal_dir.mkdir(parents=True)
        (journal_dir / "myapp.md").write_text("Tests failed in test_auth.py")

        result = handler.handle(ctx)
        assert "Diagnosis queued" in result
        assert "myapp" in result

        missions = (ctx.instance_dir / "missions.md").read_text()
        assert "Diagnose and fix" in missions
        assert "Fix auth bug" in missions
        assert "stagnation:tool_loop" in missions
        assert "Tests failed in test_auth.py" in missions

    def test_queues_mission_without_journal(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Deploy fix ❌ (2026-05-20 14:30)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result = handler.handle(ctx)
        assert "Diagnosis queued" in result

        missions = (ctx.instance_dir / "missions.md").read_text()
        assert "Diagnose and fix" in missions
        assert "Journal context" not in missions

    def test_mission_queued_urgent(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n- Existing task\n\n"
            "## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Build broke ❌ (2026-05-20 14:30)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        handler.handle(ctx)

        missions = (ctx.instance_dir / "missions.md").read_text()
        pending_start = missions.index("## Pending")
        existing_pos = missions.index("Existing task")
        diagnose_pos = missions.index("Diagnose and fix")
        assert diagnose_pos < existing_pos

    def test_duplicate_detection(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- Fix it ❌ (2026-05-20 14:30)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        result1 = handler.handle(ctx)
        assert "Diagnosis queued" in result1

        result2 = handler.handle(ctx)
        assert "already queued" in result2

    def test_with_project_filter_arg(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- [project:web] Web fail ❌ (2026-05-20 16:00)\n"
            "- [project:api] API fail ❌ (2026-05-20 14:00)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        ctx.args = "api"
        result = handler.handle(ctx)
        assert "Diagnosis queued" in result
        assert "api" in result.lower()

    def test_project_filter_no_match(self, handler, ctx):
        content = (
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n\n"
            "- [project:web] Web fail ❌ (2026-05-20 16:00)\n"
        )
        (ctx.instance_dir / "missions.md").write_text(content)

        ctx.args = "mobile"
        result = handler.handle(ctx)
        assert "No failed missions" in result
        assert "mobile" in result
