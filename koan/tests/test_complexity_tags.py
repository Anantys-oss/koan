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
        tmp = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)

    def teardown_method(self, method):
        """Clean up any temp files left by the test."""
        pass  # Files cleaned individually

    def test_basic_round_trip(self):
        content = "## Pending\n- Fix typo in README\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Fix typo in README", "trivial", path)
            updated = path.read_text()
            assert "[complexity:trivial]" in updated
            # Verify round-trip extraction
            line = [l for l in updated.splitlines() if "Fix typo" in l][0]
            assert extract_complexity_tag(line) == "trivial"
        finally:
            path.unlink(missing_ok=True)

    def test_tag_inserted_before_timestamp(self):
        content = "## Pending\n- Fix typo ⏳(2024-01-01T10:00)\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Fix typo ⏳(2024-01-01T10:00)", "simple", path)
            updated = path.read_text()
            line = [l for l in updated.splitlines() if "Fix typo" in l][0]
            # Tag should appear before the timestamp marker
            tag_pos = line.index("[complexity:simple]")
            ts_pos = line.index("⏳")
            assert tag_pos < ts_pos
        finally:
            path.unlink(missing_ok=True)

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
            path.unlink(missing_ok=True)

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
            path.unlink(missing_ok=True)

    def test_no_match_leaves_file_unchanged(self):
        content = "## Pending\n- Some other mission\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("Nonexistent mission", "trivial", path)
            updated = path.read_text()
            assert updated == content
        finally:
            path.unlink(missing_ok=True)

    def test_project_tag_coexists(self):
        content = "## Pending\n- [project:koan] fix the thing\n## Done\n"
        path = self._make_missions(content)
        try:
            tag_complexity_in_pending("[project:koan] fix the thing", "simple", path)
            updated = path.read_text()
            assert "[complexity:simple]" in updated
            assert "[project:koan]" in updated
        finally:
            path.unlink(missing_ok=True)
