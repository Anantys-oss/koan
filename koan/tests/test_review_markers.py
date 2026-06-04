"""Tests for app.review_markers — HTML comment marker helpers."""

import pytest

from app.review_markers import (
    SUMMARY_TAG,
    COMMIT_IDS_START,
    COMMIT_IDS_END,
    extract_between_markers,
    extract_commit_shas,
    build_hidden_commit_block,
    replace_commit_block,
    remove_section,
    wrap_section,
    replace_section,
)

# Test-only markers for remove_section tests (no longer used in production)
_TEST_START = "<!-- test-start -->"
_TEST_END = "<!-- test-end -->"


# ---------------------------------------------------------------------------
# extract_between_markers
# ---------------------------------------------------------------------------

class TestExtractBetweenMarkers:
    def test_extracts_content(self):
        body = f"before{COMMIT_IDS_START}sha1\nsha2{COMMIT_IDS_END}after"
        result = extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END)
        assert result == "sha1\nsha2"

    def test_returns_none_when_start_absent(self):
        body = f"no markers here{COMMIT_IDS_END}"
        assert extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END) is None

    def test_returns_none_when_end_absent(self):
        body = f"{COMMIT_IDS_START}sha1\nsha2 — end tag missing"
        assert extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END) is None

    def test_returns_none_when_both_absent(self):
        assert extract_between_markers("plain text", COMMIT_IDS_START, COMMIT_IDS_END) is None

    def test_returns_empty_string_for_empty_content(self):
        body = f"{COMMIT_IDS_START}{COMMIT_IDS_END}"
        result = extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END)
        assert result == ""

    def test_content_with_double_dashes(self):
        """Content containing '--' (e.g. commit messages) is handled."""
        body = f"{COMMIT_IDS_START}abc--def{COMMIT_IDS_END}"
        assert extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END) == "abc--def"

    def test_returns_none_when_tags_in_wrong_order(self):
        """End tag before start tag → None."""
        body = f"{COMMIT_IDS_END}content{COMMIT_IDS_START}"
        assert extract_between_markers(body, COMMIT_IDS_START, COMMIT_IDS_END) is None


# ---------------------------------------------------------------------------
# remove_section
# ---------------------------------------------------------------------------

class TestRemoveSection:
    def test_removes_tagged_block(self):
        body = f"before\n{_TEST_START}temp content{_TEST_END}\nafter"
        result = remove_section(body, _TEST_START, _TEST_END)
        assert _TEST_START not in result
        assert _TEST_END not in result
        assert "temp content" not in result
        assert "before" in result
        assert "after" in result

    def test_returns_unchanged_when_start_absent(self):
        body = "no markers"
        assert remove_section(body, _TEST_START, _TEST_END) == body

    def test_returns_unchanged_when_end_absent(self):
        body = f"{_TEST_START}content without end"
        assert remove_section(body, _TEST_START, _TEST_END) == body

    def test_removes_only_the_tagged_block(self):
        body = f"header\n{_TEST_START}temp{_TEST_END}\nfooter"
        result = remove_section(body, _TEST_START, _TEST_END)
        assert result == "header\n\nfooter"

    def test_empty_content_block(self):
        body = f"{_TEST_START}{_TEST_END}"
        assert remove_section(body, _TEST_START, _TEST_END) == ""


# ---------------------------------------------------------------------------
# wrap_section
# ---------------------------------------------------------------------------

class TestWrapSection:
    def test_wraps_content(self):
        result = wrap_section("hello", COMMIT_IDS_START, COMMIT_IDS_END)
        assert result == f"{COMMIT_IDS_START}hello{COMMIT_IDS_END}"

    def test_wraps_empty_content(self):
        result = wrap_section("", COMMIT_IDS_START, COMMIT_IDS_END)
        assert result == f"{COMMIT_IDS_START}{COMMIT_IDS_END}"

    def test_roundtrip_with_extract(self):
        content = "sha-abc\nsha-def"
        wrapped = wrap_section(content, COMMIT_IDS_START, COMMIT_IDS_END)
        extracted = extract_between_markers(wrapped, COMMIT_IDS_START, COMMIT_IDS_END)
        assert extracted == content


# ---------------------------------------------------------------------------
# replace_section
# ---------------------------------------------------------------------------

class TestReplaceSection:
    def test_appends_when_absent(self):
        body = "## Review\n\nLooks good."
        result = replace_section(body, COMMIT_IDS_START, COMMIT_IDS_END, "sha1")
        assert result.endswith(f"{COMMIT_IDS_START}sha1{COMMIT_IDS_END}")
        assert "## Review" in result

    def test_replaces_existing_block(self):
        body = f"header\n{COMMIT_IDS_START}old-sha{COMMIT_IDS_END}\nfooter"
        result = replace_section(body, COMMIT_IDS_START, COMMIT_IDS_END, "new-sha")
        assert "old-sha" not in result
        assert f"{COMMIT_IDS_START}new-sha{COMMIT_IDS_END}" in result
        assert "header" in result
        assert "footer" in result

    def test_idempotent_on_same_content(self):
        body = f"text\n{COMMIT_IDS_START}sha1{COMMIT_IDS_END}"
        result1 = replace_section(body, COMMIT_IDS_START, COMMIT_IDS_END, "sha1")
        result2 = replace_section(result1, COMMIT_IDS_START, COMMIT_IDS_END, "sha1")
        assert result1 == result2

    def test_handles_malformed_missing_end_tag(self):
        """Start present but end absent — appends new block."""
        body = f"text\n{COMMIT_IDS_START}orphan start"
        result = replace_section(body, COMMIT_IDS_START, COMMIT_IDS_END, "sha1")
        assert result.endswith(f"{COMMIT_IDS_START}sha1{COMMIT_IDS_END}")

    def test_replaces_with_empty_content(self):
        body = f"{COMMIT_IDS_START}old{COMMIT_IDS_END}"
        result = replace_section(body, COMMIT_IDS_START, COMMIT_IDS_END, "")
        assert result == f"{COMMIT_IDS_START}{COMMIT_IDS_END}"


# ---------------------------------------------------------------------------
# Hidden commit block (single HTML comment format)
# ---------------------------------------------------------------------------

class TestBuildHiddenCommitBlock:
    def test_single_sha(self):
        result = build_hidden_commit_block(["abc123"])
        assert result == "<!-- koan-commits\nabc123\n-->"

    def test_multiple_shas(self):
        result = build_hidden_commit_block(["abc123", "def456"])
        assert result == "<!-- koan-commits\nabc123\ndef456\n-->"

    def test_fully_hidden_no_visible_text(self):
        """Entire block is a single HTML comment — nothing renders visibly."""
        result = build_hidden_commit_block(["abc123"])
        assert result.startswith("<!--")
        assert result.endswith("-->")


class TestExtractCommitShas:
    def test_extracts_from_new_format(self):
        body = "review text\n<!-- koan-commits\nabc123\ndef456\n-->"
        assert extract_commit_shas(body) == ["abc123", "def456"]

    def test_extracts_from_legacy_format(self):
        body = f"review text\n{COMMIT_IDS_START}abc123\ndef456{COMMIT_IDS_END}"
        assert extract_commit_shas(body) == ["abc123", "def456"]

    def test_prefers_new_format_over_legacy(self):
        body = (
            f"{COMMIT_IDS_START}old1\nold2{COMMIT_IDS_END}"
            "\n<!-- koan-commits\nnew1\nnew2\n-->"
        )
        assert extract_commit_shas(body) == ["new1", "new2"]

    def test_returns_empty_list_when_no_block(self):
        assert extract_commit_shas("plain review text") == []

    def test_strips_whitespace_from_shas(self):
        body = "<!-- koan-commits\n  abc123  \n  def456  \n-->"
        assert extract_commit_shas(body) == ["abc123", "def456"]

    def test_skips_blank_lines(self):
        body = "<!-- koan-commits\nabc123\n\ndef456\n-->"
        assert extract_commit_shas(body) == ["abc123", "def456"]


class TestReplaceCommitBlock:
    def test_appends_when_no_block_exists(self):
        body = "review text"
        result = replace_commit_block(body, ["abc123"])
        assert "abc123" in result
        assert "<!-- koan-commits" in result
        assert result.startswith("review text")

    def test_replaces_legacy_block(self):
        body = f"review\n{COMMIT_IDS_START}old{COMMIT_IDS_END}\nfooter"
        result = replace_commit_block(body, ["new1"])
        assert COMMIT_IDS_START not in result
        assert "old" not in result
        assert "new1" in result
        assert "footer" in result

    def test_replaces_new_format_block(self):
        body = "review\n<!-- koan-commits\nold\n-->\nfooter"
        result = replace_commit_block(body, ["new1"])
        assert "old" not in result
        assert "new1" in result
        assert "footer" in result

    def test_roundtrip(self):
        shas = ["abc123", "def456", "ghi789"]
        block = build_hidden_commit_block(shas)
        extracted = extract_commit_shas(block)
        assert extracted == shas
