"""Tests for koan/app/diff_compressor.py."""
import pytest

from app.diff_compressor import (
    CompressedDiff,
    FileDiff,
    compress_diff,
    estimate_tokens,
    parse_diff_hunks,
)

# ---------------------------------------------------------------------------
# Synthetic diff fixtures
# ---------------------------------------------------------------------------

DIFF_TWO_FILES = """\
diff --git a/foo.py b/foo.py
index aaa..bbb 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 def hello():
-    pass
+    return "hello"
+
 # end
@@ -10,2 +11,3 @@
 x = 1
+y = 2
 z = 3
diff --git a/config.yaml b/config.yaml
index ccc..ddd 100644
--- a/config.yaml
+++ b/config.yaml
@@ -1,2 +1,3 @@
 key: value
+new_key: new_value
 other: other
"""

DIFF_BINARY = """\
diff --git a/image.png b/image.png
index 000..fff 100644
Binary files a/image.png and b/image.png differ
diff --git a/main.py b/main.py
index aaa..bbb 100644
--- a/main.py
+++ b/main.py
@@ -1,2 +1,3 @@
 x = 1
+y = 2
 z = 3
"""

DIFF_RENAME = """\
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
index aaa..bbb 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,2 +1,2 @@
-old = True
+new = True
"""


# ---------------------------------------------------------------------------
# parse_diff_hunks
# ---------------------------------------------------------------------------


class TestParseDiffHunks:
    def test_parses_two_files(self):
        result = parse_diff_hunks(DIFF_TWO_FILES)
        assert len(result) == 2
        paths = [fd.path for fd in result]
        assert "foo.py" in paths
        assert "config.yaml" in paths

    def test_hunks_split_correctly(self):
        result = parse_diff_hunks(DIFF_TWO_FILES)
        foo = next(fd for fd in result if fd.path == "foo.py")
        assert len(foo.hunks) == 2
        assert foo.hunks[0].startswith("@@")
        assert foo.hunks[1].startswith("@@")

    def test_single_hunk_file(self):
        result = parse_diff_hunks(DIFF_TWO_FILES)
        cfg = next(fd for fd in result if fd.path == "config.yaml")
        assert len(cfg.hunks) == 1

    def test_binary_file_detected(self):
        result = parse_diff_hunks(DIFF_BINARY)
        png = next(fd for fd in result if "image.png" in fd.path)
        assert png.is_binary is True

    def test_non_binary_not_flagged(self):
        result = parse_diff_hunks(DIFF_BINARY)
        py = next(fd for fd in result if fd.path == "main.py")
        assert py.is_binary is False

    def test_header_preserved(self):
        result = parse_diff_hunks(DIFF_TWO_FILES)
        foo = next(fd for fd in result if fd.path == "foo.py")
        assert "diff --git" in foo.header
        assert "--- a/foo.py" in foo.header
        assert "+++ b/foo.py" in foo.header

    def test_renamed_file(self):
        result = parse_diff_hunks(DIFF_RENAME)
        assert len(result) == 1
        fd = result[0]
        assert "new_name.py" in fd.path
        assert "rename from" in fd.header

    def test_empty_diff(self):
        assert parse_diff_hunks("") == []
        assert parse_diff_hunks("   \n") == []

    def test_full_text_roundtrip(self):
        """full_text() should reconstruct the original block faithfully."""
        result = parse_diff_hunks(DIFF_TWO_FILES)
        reconstructed = "".join(fd.full_text() for fd in result)
        # Every line from the original diff should appear somewhere.
        for line in DIFF_TWO_FILES.strip().splitlines():
            assert line in reconstructed


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_four_chars_one_token(self):
        assert estimate_tokens("abcd") == 1

    def test_approximation(self):
        text = "x" * 400
        assert estimate_tokens(text) == 100


# ---------------------------------------------------------------------------
# compress_diff — within budget
# ---------------------------------------------------------------------------


class TestCompressDiff:
    def test_empty_diff(self):
        result = compress_diff("")
        assert result.diff_text == ""
        assert result.skipped_files == []

    def test_small_diff_fits_entirely(self):
        result = compress_diff(DIFF_TWO_FILES, token_budget=100_000)
        assert "foo.py" in result.diff_text
        assert "config.yaml" in result.diff_text
        assert result.skipped_files == []

    def test_language_priority_ordering(self):
        """Python file should appear before yaml file in compressed output."""
        result = compress_diff(DIFF_TWO_FILES, token_budget=100_000)
        py_pos = result.diff_text.find("foo.py")
        yaml_pos = result.diff_text.find("config.yaml")
        assert py_pos < yaml_pos

    def test_skips_low_priority_files_when_budget_tight(self):
        # Give just enough budget for the Python file but not the yaml.
        result = parse_diff_hunks(DIFF_TWO_FILES)
        py_fd = next(fd for fd in result if fd.path == "foo.py")
        tight_budget = py_fd.token_estimate() + 1  # barely enough for .py

        compressed = compress_diff(DIFF_TWO_FILES, token_budget=tight_budget)
        assert "foo.py" in compressed.diff_text
        assert "config.yaml" in compressed.skipped_files

    def test_at_least_one_hunk_always_included(self):
        """Even if the single file exceeds budget, include its first hunk."""
        # Build a diff where the file is larger than a tiny budget.
        big_hunk = "@@ -1,100 +1,101 @@\n" + "+line\n" * 100
        big_diff = (
            "diff --git a/big.py b/big.py\n"
            "index aaa..bbb 100644\n"
            "--- a/big.py\n"
            "+++ b/big.py\n"
            + big_hunk
        )
        compressed = compress_diff(big_diff, token_budget=1)
        # The first (and only) hunk must be included.
        assert "@@ -1,100" in compressed.diff_text

    def test_partial_file_in_skipped(self):
        """A file included only partially appears as '<path> (partial)'."""
        hunk_a = "@@ -1,3 +1,4 @@\n" + "+line\n" * 3
        hunk_b = "@@ -10,3 +11,4 @@\n" + "+line\n" * 3
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "index aaa..bbb 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            + hunk_a
            + hunk_b
        )
        # Budget that fits header + first hunk but not second hunk.
        fd = parse_diff_hunks(diff)[0]
        header_tokens = estimate_tokens(fd.header)
        hunk_a_tokens = estimate_tokens(hunk_a)
        budget = header_tokens + hunk_a_tokens + 1  # just fits header + hunk_a

        compressed = compress_diff(diff, token_budget=budget)
        assert "foo.py (partial)" in compressed.skipped_files

    def test_binary_file_always_included(self):
        """Binary files are always included (header only, zero token cost)."""
        compressed = compress_diff(DIFF_BINARY, token_budget=1)
        assert "image.png" in compressed.diff_text
        # Binary file never in skipped list.
        assert not any("image.png" in s for s in compressed.skipped_files)

    def test_skipped_files_records_paths(self):
        result = parse_diff_hunks(DIFF_TWO_FILES)
        py_fd = next(fd for fd in result if fd.path == "foo.py")
        tight_budget = py_fd.token_estimate() + 1

        compressed = compress_diff(DIFF_TWO_FILES, token_budget=tight_budget)
        assert len(compressed.skipped_files) == 1
        assert compressed.skipped_files[0] == "config.yaml"
