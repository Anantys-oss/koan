"""Tests for complexity tag helpers in app.missions."""

import os
import tempfile
from pathlib import Path

import pytest

from app.missions import (
    extract_complexity_tag,
    tag_complexity_in_pending,
    DEFAULT_SKELETON,
)


# ---------------------------------------------------------------------------
# extract_complexity_tag
# ---------------------------------------------------------------------------

class TestExtractComplexityTag:
    def test_trivial(self):
        assert extract_complexity_tag("Fix typo [complexity:trivial] ⏳(2024-01-01T10:00)") == "trivial"

    def test_simple(self):
        assert extract_complexity_tag("Do something [complexity:simple]") == "simple"

    def test_medium(self):
        assert extract_complexity_tag("Some work [complexity:medium]") == "medium"

    def test_complex(self):
        assert extract_complexity_tag("Big work [complexity:complex]") == "complex"

    def test_case_insensitive(self):
        assert extract_complexity_tag("task [complexity:TRIVIAL]") == "trivial"

    def test_no_tag_returns_none(self):
        assert extract_complexity_tag("fix typo in README") is None

    def test_project_tag_not_confused(self):
        """[project:name] must not be extracted as a complexity tag."""
        assert extract_complexity_tag("[project:koan] fix bug") is None

    def test_tdd_tag_not_confused(self):
        assert extract_complexity_tag("[tdd] fix bug") is None

    def test_empty_string(self):
        assert extract_complexity_tag("") is None

    def test_coexists_with_project_tag(self):
        line = "[project:koan] fix typo [complexity:trivial] ⏳(2024-01-01T10:00)"
        assert extract_complexity_tag(line) == "trivial"


# ---------------------------------------------------------------------------
# tag_complexity_in_pending — round-trip via real file
# ---------------------------------------------------------------------------

class TestTagComplexityInPending:
    def _make_missions(self, content: str) -> Path:
        """Create a temp directory with missions.md so the store can find it."""
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "missions.md"
        path.write_text(content)
        return path

    def teardown_method(self, method):
        pass

    def test_basic_round_trip(self):
        content = "## Pending\n- Fix typo in README\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Fix typo in README", "trivial", path)
            updated = path.read_text()
            assert "[complexity:trivial]" in updated
            line = [l for l in updated.splitlines() if "Fix typo" in l][0]
            assert extract_complexity_tag(line) == "trivial"
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_tag_inserted_before_timestamp(self):
        content = "## Pending\n- Fix typo ⏳(2024-01-01T10:00)\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Fix typo ⏳(2024-01-01T10:00)", "simple", path)
            updated = path.read_text()
            line = [l for l in updated.splitlines() if "Fix typo" in l][0]
            tag_pos = line.index("[complexity:simple]")
            ts_pos = line.index("⏳")
            assert tag_pos < ts_pos
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_idempotent_does_not_double_tag(self):
        """Calling tag_complexity_in_pending twice must not add a second tag."""
        content = "## Pending\n- Fix bug\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Fix bug", "medium", path)
            tag_complexity_in_pending("Fix bug [complexity:medium]", "medium", path)
            updated = path.read_text()
            assert updated.count("[complexity:") == 1
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_only_tags_pending_section(self):
        """Missions in Done must not be tagged."""
        content = (
            "## Pending\n- New mission\n"
            "## Done\n- Old mission\n"
        )
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Old mission", "trivial", path)
            updated = path.read_text()
            done_section = updated.split("## Done")[1]
            assert "[complexity:" not in done_section
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_no_match_leaves_file_unchanged(self):
        content = "## Pending\n- Some other mission\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Nonexistent mission", "trivial", path)
            updated = path.read_text()
            assert "[complexity:" not in updated
            assert "Some other mission" in updated
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_project_tag_coexists(self):
        content = "## Pending\n- [project:koan] fix the thing\n## Done\n"
        path = self._make_missions(content)
        try:
            # Pass just the text (no project prefix) — project is stored separately
            tag_complexity_in_pending("fix the thing", "simple", path)
            updated = path.read_text()
            assert "[complexity:simple]" in updated
            assert "[project:koan]" in updated
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# Complexity tag must not break mission lifecycle transitions.
# Regression: tag_complexity_in_pending inserts [complexity:X] between the
# mission text and the ⏳ timestamp. The needle used by start_mission /
# complete_mission / fail_mission was captured before the tag was added,
# so the substring match fails and mission transitions silently do nothing.
# ---------------------------------------------------------------------------

class TestComplexityTagLifecycleInteraction:
    """Verify lifecycle functions still find missions after complexity tagging."""

    SKELETON = (
        "# Missions\n\n## CI\n\n## Pending\n\n"
        "- [project:myapp] Fix the login bug ⏳(2026-05-24T16:01)\n\n"
        "## In Progress\n\n## Done\n\n## Failed\n"
    )

    NEEDLE = "Fix the login bug ⏳(2026-05-24T16:01)"

    def _tag_content(self, content: str) -> str:
        """Simulate what tag_complexity_in_pending does, inline."""
        return content.replace(
            "Fix the login bug ⏳",
            "Fix the login bug [complexity:medium] ⏳",
        )

    def test_start_mission_after_complexity_tag(self):
        tagged = self._tag_content(self.SKELETON)
        from app.missions import start_mission, parse_sections
        result = start_mission(tagged, self.NEEDLE)
        sections = parse_sections(result)
        assert len(sections["in_progress"]) == 1, "Mission must move to In Progress"
        assert sections["pending"] == [] or all(
            "Fix the login bug" not in p for p in sections["pending"]
        ), "Mission must be removed from Pending"

    def test_complete_mission_after_complexity_tag(self):
        tagged = self._tag_content(self.SKELETON)
        from app.missions import complete_mission, parse_sections
        result = complete_mission(tagged, self.NEEDLE)
        sections = parse_sections(result)
        assert len(sections["done"]) == 1, "Mission must move to Done"
        assert all(
            "Fix the login bug" not in p for p in sections["pending"]
        ), "Mission must be removed from Pending"

    def test_fail_mission_after_complexity_tag(self):
        tagged = self._tag_content(self.SKELETON)
        from app.missions import fail_mission, parse_sections
        result = fail_mission(tagged, self.NEEDLE)
        sections = parse_sections(result)
        assert len(sections["failed"]) == 1, "Mission must move to Failed"
        assert all(
            "Fix the login bug" not in p for p in sections["pending"]
        ), "Mission must be removed from Pending"

    def test_no_complexity_tag_still_works(self):
        """Verify the fix doesn't regress the normal (no-tag) path."""
        from app.missions import start_mission, parse_sections
        result = start_mission(self.SKELETON, self.NEEDLE)
        sections = parse_sections(result)
        assert len(sections["in_progress"]) == 1
        assert all(
            "Fix the login bug" not in p for p in sections["pending"]
        )
