"""Tests for review_runner.py — code review pipeline for PRs."""

import copy
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.review_runner import (
    build_review_prompt,
    fetch_repliable_comments,
    load_project_learnings,
    run_review,
    _collapse_old_review,
    _detect_plan_url,
    _fetch_plan_body,
    _truncate_plan,
    _resolve_plan_body,
    _extract_review_body,
    _format_repliable_comments,
    _parse_review_json,
    _format_review_as_markdown,
    _extract_json_text,
    _build_review_footer,
    _post_review_comment,
    _post_comment_replies,
    _fetch_pr_commit_shas,
    _safe_code_fence,
    _fix_nested_fences,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pr_context():
    """Minimal PR context dict matching fetch_pr_context output."""
    return {
        "title": "Fix auth bypass",
        "body": "Fixes #42 by adding proper token validation.",
        "branch": "fix-auth",
        "base": "main",
        "state": "OPEN",
        "author": "dev123",
        "url": "https://github.com/owner/repo/pull/42",
        "diff": "--- a/auth.py\n+++ b/auth.py\n@@ -1,3 +1,5 @@\n+import jwt\n",
        "review_comments": "",
        "reviews": "",
        "issue_comments": "",
    }


@pytest.fixture
def review_skill_dir(tmp_path):
    """Create a skill dir with a review prompt."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "review.md").write_text(
        "Review PR: {TITLE}\nAuthor: {AUTHOR}\nBranch: {BRANCH} -> {BASE}\n"
        "Body: {BODY}\n{SKIPPED_FILES}Diff: {DIFF}\n"
        "Reviews: {REVIEWS}\nComments: {REVIEW_COMMENTS}\n"
        "Issue: {ISSUE_COMMENTS}\n"
        "Repliable: {REPLIABLE_COMMENTS}\n"
    )
    (prompts_dir / "reflect.md").write_text(
        "Reflect: {FINDINGS_JSON}\nDiff: {DIFF}"
    )
    return tmp_path


@pytest.fixture
def plan_review_skill_dir(tmp_path):
    """Create a skill dir with both review.md and review-with-plan.md prompts."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    base = (
        "{TITLE}\n{AUTHOR}\n{BRANCH}\n{BASE}\n{BODY}\n{SKIPPED_FILES}{DIFF}\n"
        "{REVIEWS}\n{REVIEW_COMMENTS}\n{ISSUE_COMMENTS}\n{REPLIABLE_COMMENTS}\n"
    )
    (prompts_dir / "review.md").write_text("Review PR: " + base)
    (prompts_dir / "review-with-plan.md").write_text("Plan Review: {PLAN}\n" + base)
    return tmp_path


# ---------------------------------------------------------------------------
# build_review_prompt
# ---------------------------------------------------------------------------

class TestBuildReviewPrompt:
    def test_with_skill_dir(self, pr_context, review_skill_dir):
        """Prompt is built from skill dir template."""
        prompt = build_review_prompt(pr_context, skill_dir=review_skill_dir)
        assert "Fix auth bypass" in prompt
        assert "dev123" in prompt
        assert "fix-auth" in prompt
        assert "main" in prompt

    def test_placeholders_substituted(self, pr_context, review_skill_dir):
        """All {PLACEHOLDER} values are substituted."""
        prompt = build_review_prompt(pr_context, skill_dir=review_skill_dir)
        assert "{TITLE}" not in prompt
        assert "{AUTHOR}" not in prompt
        assert "{BRANCH}" not in prompt
        assert "{BASE}" not in prompt
        assert "{BODY}" not in prompt
        assert "{DIFF}" not in prompt
        assert "{SKIPPED_FILES}" not in prompt

    def test_compress_diff_called_for_large_diff(self, pr_context, review_skill_dir):
        """compress_diff is invoked; large diffs produce shorter DIFF in prompt."""
        # Build a diff large enough that compress_diff trims something.
        big_hunk = "@@ -1,100 +1,101 @@\n" + "+line\n" * 200
        pr_context = dict(pr_context)
        pr_context["diff"] = (
            "diff --git a/main.py b/main.py\nindex aaa..bbb 100644\n"
            "--- a/main.py\n+++ b/main.py\n" + big_hunk
            + "diff --git a/config.yaml b/config.yaml\nindex ccc..ddd 100644\n"
            "--- a/config.yaml\n+++ b/config.yaml\n"
            "@@ -1,2 +1,3 @@\n key: value\n+new_key: new_value\n other: other\n"
        )
        raw_diff = pr_context["diff"]

        from app.diff_compressor import compress_diff as real_compress
        call_args = {}

        def spy_compress(diff, token_budget=80_000):
            result = real_compress(diff, token_budget)
            call_args["called"] = True
            call_args["result"] = result
            return result

        with patch("app.review_runner.compress_diff", side_effect=spy_compress):
            build_review_prompt(pr_context, skill_dir=review_skill_dir)

        assert call_args.get("called"), "compress_diff was not called"

    def test_skipped_files_note_absent_for_small_diff(self, pr_context, review_skill_dir):
        """No skipped-files note when diff fits within budget."""
        prompt = build_review_prompt(pr_context, skill_dir=review_skill_dir)
        assert "omitted due to size" not in prompt

    def test_memory_task_text_includes_title_body_diff_slice(
        self, pr_context, review_skill_dir,
    ):
        """Project-memory scoring should use title + body + diff slice — not
        title + branch — so learnings recall actually gets useful signal.

        Branch names like ``koan/fix-issue-123`` produce near-zero Jaccard
        overlap with anything; the diff is where filenames and module names
        live. Regression guard for the broadened task_text.
        """
        from unittest.mock import patch

        # Make the diff content unmistakable so we can assert it reached the
        # task_text and the branch did not.
        pr_context["diff"] = (
            "--- a/koan/app/quota_handler.py\n"
            "+++ b/koan/app/quota_handler.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+def detect_quota_exhaustion(stdout: str) -> bool:\n"
        )
        with patch(
            "app.skill_memory.build_memory_block_for_skill", return_value="",
        ) as mock_build:
            build_review_prompt(
                pr_context, skill_dir=review_skill_dir, project_path="/fake/proj",
            )
        assert mock_build.call_count == 1
        _project_path, task_text = mock_build.call_args.args[:2]
        assert "Fix auth bypass" in task_text  # title
        assert "token validation" in task_text  # body
        assert "quota_handler.py" in task_text  # diff signal
        assert "fix-auth" not in task_text     # branch must not be the main signal


# ---------------------------------------------------------------------------
# load_project_learnings
# ---------------------------------------------------------------------------

class TestLoadProjectLearnings:
    def test_returns_content_when_file_present(self, tmp_path, monkeypatch):
        """Returns formatted section when learnings.md exists."""
        instance_dir = tmp_path / "instance" / "memory" / "projects" / "my-project"
        instance_dir.mkdir(parents=True)
        (instance_dir / "learnings.md").write_text("Always use type hints.")

        import app.review_runner as rr_mod
        monkeypatch.setattr(rr_mod, "KOAN_ROOT", tmp_path)

        result = load_project_learnings("my-project")
        assert "Project best practices" in result
        assert "Always use type hints." in result

    def test_returns_empty_when_file_absent(self, tmp_path, monkeypatch):
        """Returns empty string when learnings.md does not exist."""
        import app.review_runner as rr_mod
        monkeypatch.setattr(rr_mod, "KOAN_ROOT", tmp_path)

        result = load_project_learnings("no-such-project")
        assert result == ""

    def test_returns_empty_when_project_name_none(self, tmp_path, monkeypatch):
        """Returns empty string when project_name is None."""
        import app.review_runner as rr_mod
        monkeypatch.setattr(rr_mod, "KOAN_ROOT", tmp_path)

        result = load_project_learnings(None)
        assert result == ""

    def test_returns_empty_when_file_empty(self, tmp_path, monkeypatch):
        """Returns empty string when learnings.md is empty."""
        instance_dir = tmp_path / "instance" / "memory" / "projects" / "proj"
        instance_dir.mkdir(parents=True)
        (instance_dir / "learnings.md").write_text("")

        import app.review_runner as rr_mod
        monkeypatch.setattr(rr_mod, "KOAN_ROOT", tmp_path)

        result = load_project_learnings("proj")
        assert result == ""


# ---------------------------------------------------------------------------
# _extract_review_body
# ---------------------------------------------------------------------------

class TestExtractReviewBody:
    def test_extracts_new_format(self):
        """Extracts from ## PR Review onward (new format)."""
        raw = (
            "Some preamble\n\n"
            "## PR Review — Fix auth bypass\n\n"
            "Good PR. One blocking issue.\n\n"
            "### 🔴 Blocking\n\n**1. Missing check** (`auth.py`)\n"
        )
        result = _extract_review_body(raw)
        assert result.startswith("## PR Review")
        assert "Fix auth bypass" in result
        assert "preamble" not in result

    def test_extracts_legacy_format(self):
        """Extracts from ## Summary onward (legacy format)."""
        raw = "Some preamble\n\n## Summary\nLooks good.\n\n## Issues\nNone."
        result = _extract_review_body(raw)
        assert result.startswith("## Summary")
        assert "Looks good." in result
        assert "preamble" not in result

    def test_prefers_new_format_over_legacy(self):
        """When both formats present, prefers ## PR Review."""
        raw = (
            "## PR Review — Title\nSummary here.\n\n"
            "## Summary\nLegacy section."
        )
        result = _extract_review_body(raw)
        assert result.startswith("## PR Review")

    def test_unstructured_output_returns_none(self):
        """Guardrail: when no structured review can be recovered, returns None
        rather than leaking raw model output to the PR."""
        raw = "This is a freeform review. Code is fine."
        assert _extract_review_body(raw) is None

    def test_empty_input_returns_none(self):
        """Empty input yields no structured review -> None."""
        assert _extract_review_body("") is None

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace is removed."""
        raw = "  \n## PR Review — Test\nClean code.\n  "
        result = _extract_review_body(raw)
        assert result.startswith("## PR Review")
        assert not result.endswith(" ")

    def test_raw_json_gets_formatted(self):
        """When output is raw JSON, converts to markdown instead of posting JSON."""
        raw = json.dumps(VALID_REVIEW_JSON)
        result = _extract_review_body(raw)
        assert "## PR Review" in result
        assert "### 🔴 Blocking" in result
        assert "Missing validation" in result
        assert '"file_comments"' not in result  # No raw JSON keys

    def test_json_with_preamble_gets_formatted(self):
        """When output has JSON with preamble, extracts and formats it."""
        raw = "Here is my review:\n\n" + json.dumps(VALID_REVIEW_JSON)
        result = _extract_review_body(raw)
        assert "## PR Review" in result
        assert '"file_comments"' not in result

    def test_captures_checklist_section(self):
        """Extracts review body including ### Checklist section."""
        raw = (
            "Some preamble\n\n"
            "## PR Review — Add validation\n\n"
            "One issue found.\n\n"
            "### 🔴 Blocking\n\n"
            "**1. Missing input validation** (`api.py`, `handle_request`)\n"
            "No validation on user input.\n\n"
            "---\n\n"
            "### Checklist\n\n"
            "- [x] No hardcoded secrets\n"
            "- [x] Error paths handle cleanup\n"
            "- [ ] Missing input validation at API boundary (see 🔴 #1)\n"
            "- [x] Tests cover new branches\n\n"
            "---\n\n"
            "### Summary\n\n"
            "Needs input validation before merge."
        )
        result = _extract_review_body(raw)
        assert result.startswith("## PR Review")
        assert "### Checklist" in result
        assert "- [ ] Missing input validation" in result
        assert "### Summary" in result
        assert "preamble" not in result


# ---------------------------------------------------------------------------
# _extract_json_text
# ---------------------------------------------------------------------------

class TestExtractJsonText:
    def test_pure_json(self):
        result = _extract_json_text('{"a": 1}')
        assert result is not None
        assert json.loads(result) == {"a": 1}

    def test_json_in_fences(self):
        result = _extract_json_text('```json\n{"a": 1}\n```')
        assert result is not None
        assert json.loads(result) == {"a": 1}

    def test_json_in_plain_fences(self):
        result = _extract_json_text('```\n{"a": 1}\n```')
        assert result is not None
        assert json.loads(result) == {"a": 1}

    def test_json_with_preamble(self):
        text = 'Here is my review:\n\n{"file_comments": [], "review_summary": {"lgtm": true, "summary": "ok", "checklist": []}}'
        result = _extract_json_text(text)
        assert result is not None
        data = json.loads(result)
        assert data["review_summary"]["lgtm"] is True

    def test_json_with_preamble_and_postamble(self):
        text = 'I analyzed the code:\n\n{"a": 1}\n\nHope this helps!'
        result = _extract_json_text(text)
        assert result is not None
        assert json.loads(result) == {"a": 1}

    def test_json_fences_with_surrounding_text(self):
        text = 'Here is the review:\n\n```json\n{"a": 1}\n```\n\nLet me know if you need more.'
        result = _extract_json_text(text)
        assert result is not None
        assert json.loads(result) == {"a": 1}

    def test_no_json(self):
        result = _extract_json_text("This is plain text with no JSON.")
        assert result is None

    def test_whitespace_only(self):
        result = _extract_json_text("   \n  ")
        assert result is None

    def test_nested_braces(self):
        obj = {"outer": {"inner": {"deep": 42}}}
        text = f"Preamble\n{json.dumps(obj)}\nPostamble"
        result = _extract_json_text(text)
        assert result is not None
        assert json.loads(result) == obj

    def test_review_json_with_preamble(self):
        """The exact bug scenario: valid review JSON with Claude preamble."""
        text = "I'll provide my review as JSON:\n\n" + json.dumps(VALID_REVIEW_JSON)
        result = _extract_json_text(text)
        assert result is not None
        data = json.loads(result)
        assert "file_comments" in data
        assert "review_summary" in data

    def test_preamble_with_github_actions_expression(self):
        """Regression: preamble mentioning ${{ ... }} must not hijack extraction.

        The first '{' in the text is inside a GitHub Actions expression, which
        a first-brace-only matcher would latch onto and fail. The real object
        must still be recovered.
        """
        obj = {"file_comments": [], "review_summary": {"lgtm": True}}
        text = (
            "The workflow uses `${{ steps.parse.outputs.name }}` inline in a "
            "shell block (an injection vector).\n\n" + json.dumps(obj)
        )
        result = _extract_json_text(text)
        assert result is not None
        assert json.loads(result)["review_summary"]["lgtm"] is True

    def test_returns_largest_object_skipping_decoys(self):
        """Picks the largest valid object, skipping a small decoy {} and a
        brace-token from preamble."""
        big = {"file_comments": [], "review_summary": {"summary": "x" * 50}}
        text = "Note ${{ x }} and an empty {} then:\n\n" + json.dumps(big)
        result = _extract_json_text(text)
        assert result is not None
        assert json.loads(result) == big

    def test_object_with_embedded_code_fences_in_strings(self):
        """JSON whose string values contain ``` fences must still extract.

        Fence-regex strategies break on embedded fences; the brace-matcher
        respects string context and recovers the whole object.
        """
        obj = {
            "file_comments": [],
            "review_summary": {
                "summary": "Use:\n```bash\ngh pr create || echo done\n```\nDone.",
            },
        }
        text = "Here is the review:\n\n```json\n" + json.dumps(obj) + "\n```\n"
        result = _extract_json_text(text)
        assert result is not None
        assert "```bash" in json.loads(result)["review_summary"]["summary"]


# ---------------------------------------------------------------------------
# _parse_review_json
# ---------------------------------------------------------------------------

VALID_REVIEW_JSON = {
    "file_comments": [
        {
            "file": "auth.py",
            "line_start": 42,
            "line_end": 42,
            "severity": "critical",
            "title": "Missing validation",
            "comment": "No input validation.",
            "code_snippet": "",
        },
    ],
    "review_summary": {
        "lgtm": False,
        "summary": "Needs validation before merge.",
        "checklist": [
            {"item": "No hardcoded secrets", "passed": True, "finding_ref": ""},
        ],
    },
}

LGTM_REVIEW_JSON = {
    "file_comments": [],
    "review_summary": {
        "lgtm": True,
        "summary": "Clean code. Merge-ready.",
        "checklist": [],
    },
}

# Mirrors the real PR #40 regression: workflow-file review where the model
# emitted narration containing a ${{ ... }} expression *before* a fenced JSON
# object whose string values themselves contain ```bash code fences.
_PR40_REVIEW_OBJ = {
    "file_comments": [
        {
            "file": ".github/workflows/evaluate.yml",
            "line_start": 88,
            "line_end": 88,
            "severity": "critical",
            "title": "Command injection via expression interpolation",
            "comment": "Move the value into an env block:\n```bash\nenv:\n  NAME: ${{ steps.parse.outputs.name }}\n```\nThen reference $NAME.",
            "code_snippet": "run: echo ${{ steps.parse.outputs.name }}",
        },
        {
            "file": ".github/workflows/evaluate.yml",
            "line_start": 90,
            "line_end": 90,
            "severity": "warning",
            "title": "`|| echo` swallows all failures",
            "comment": "Check the exit code instead.",
            "code_snippet": "|| echo \"exists\"",
        },
        {
            "file": "scripts/validate.js",
            "line_start": 10,
            "line_end": 10,
            "severity": "suggestion",
            "title": "Reject symlinks in traversal",
            "comment": "Use isSymbolicLink().",
            "code_snippet": "",
        },
    ],
    "review_summary": {
        "lgtm": False,
        "summary": "Solid security-focused PR with two items to address.",
        "checklist": [
            {"item": "No command injection", "passed": False, "finding_ref": "critical #1"},
        ],
    },
    "comment_replies": [],
}

PR40_LIKE_RAW = (
    "Now I have the full picture. The workflow uses "
    "`${{ steps.parse.outputs.name }}` inline in shell `run:` blocks "
    "(a known GitHub Actions injection vector).\n\n"
    "Let me also verify the parse step is safe.\n\n"
    "Good — the parse step uses `${GITHUB_REF#refs/tags/}` which is safe.\n\n"
    "```json\n" + json.dumps(_PR40_REVIEW_OBJ) + "\n```\n"
)


class TestParseReviewJson:
    def test_valid_json(self):
        raw = json.dumps(VALID_REVIEW_JSON)
        result = _parse_review_json(raw)
        assert result is not None
        assert result["review_summary"]["lgtm"] is False
        assert len(result["file_comments"]) == 1

    def test_valid_json_in_fences(self):
        raw = f"```json\n{json.dumps(VALID_REVIEW_JSON)}\n```"
        result = _parse_review_json(raw)
        assert result is not None

    def test_lgtm_review(self):
        raw = json.dumps(LGTM_REVIEW_JSON)
        result = _parse_review_json(raw)
        assert result is not None
        assert result["review_summary"]["lgtm"] is True
        assert result["file_comments"] == []

    def test_invalid_json(self):
        result = _parse_review_json("not json at all")
        assert result is None

    def test_truncated_json(self):
        raw = '{"file_comments": [{"file": "a.py"'
        result = _parse_review_json(raw)
        assert result is None

    def test_valid_json_but_wrong_schema(self):
        raw = json.dumps({"unrelated": "data"})
        result = _parse_review_json(raw)
        assert result is None

    def test_missing_severity(self):
        data = {
            "file_comments": [{
                "file": "a.py", "line_start": 1, "line_end": 1,
                "severity": "invalid_severity",
                "title": "t", "comment": "c", "code_snippet": "",
            }],
            "review_summary": {"lgtm": False, "summary": "s", "checklist": []},
        }
        result = _parse_review_json(json.dumps(data))
        assert result is None

    def test_markdown_text_returns_none(self):
        raw = "## PR Review — Title\n\nGood code.\n\n### Summary\n\nLGTM."
        result = _parse_review_json(raw)
        assert result is None

    def test_json_with_preamble_text(self):
        """Parses valid JSON even when surrounded by preamble text."""
        raw = "Here is my analysis:\n\n" + json.dumps(VALID_REVIEW_JSON)
        result = _parse_review_json(raw)
        assert result is not None
        assert result["review_summary"]["lgtm"] is False

    def test_json_with_preamble_and_postamble(self):
        """Parses valid JSON with both preamble and postamble."""
        raw = (
            "I've analyzed the code changes.\n\n"
            + json.dumps(LGTM_REVIEW_JSON)
            + "\n\nLet me know if you need more details."
        )
        result = _parse_review_json(raw)
        assert result is not None
        assert result["review_summary"]["lgtm"] is True

    def test_json_in_fences_with_surrounding_text(self):
        """Parses JSON from code fences embedded in surrounding text."""
        raw = (
            "Here is the review:\n\n"
            f"```json\n{json.dumps(VALID_REVIEW_JSON)}\n```\n\n"
            "Hope this helps."
        )
        result = _parse_review_json(raw)
        assert result is not None

    def test_pr40_regression_parses_and_renders_buckets(self):
        """The PR #40 regression end-to-end: narration with a ${{ }} expression
        plus a fenced JSON object containing embedded ```bash fences must parse
        and render into severity buckets (not be posted as raw JSON)."""
        result = _parse_review_json(PR40_LIKE_RAW)
        assert result is not None
        assert {"file_comments", "review_summary"} <= result.keys()
        assert len(result["file_comments"]) == 3

        md = _format_review_as_markdown(result, title="Security hardening")
        assert "## PR Review — Security hardening" in md
        assert "### 🔴 Blocking" in md
        assert "### 🟡 Important" in md
        assert "### 🟢 Suggestions" in md
        # The raw JSON envelope must not leak into the rendered comment.
        assert '"file_comments"' not in md
        assert "```json" not in md

    def test_pr1178_terse_lgtm_without_checklist_survives(self):
        """Regression for esphome/device-builder#1178.

        The model produced a complete, useful LGTM review but omitted
        ``review_summary.checklist`` (allowed to be empty for trivial PRs).
        Strict validation previously discarded the whole review and posted
        the "could not be formatted" placeholder. The terse review must now
        parse, with the checklist backfilled to an empty list.
        """
        raw = json.dumps({
            "file_comments": [],
            "review_summary": {
                "lgtm": True,
                "summary": "Clean, behavioral no-op refactor.",
            },
        })
        result = _parse_review_json(raw)
        assert result is not None
        assert result["review_summary"]["lgtm"] is True
        assert result["review_summary"]["checklist"] == []

    def test_file_comment_without_code_snippet_survives(self):
        """A finding that omits the optional code_snippet must not sink the
        whole review — the sentinel empty string is backfilled."""
        raw = json.dumps({
            "file_comments": [{
                "file": "script/sync_boards.py",
                "line_start": 392, "line_end": 392,
                "severity": "suggestion",
                "title": "Consider a comment", "comment": "Explain the cast.",
            }],
            "review_summary": {"lgtm": True, "summary": "Minor nit.", "checklist": []},
        })
        result = _parse_review_json(raw)
        assert result is not None
        assert result["file_comments"][0]["code_snippet"] == ""

    def test_lgtm_derived_from_severities_when_omitted(self):
        """When the model omits lgtm, it is derived: blocking iff any
        critical/warning finding is present."""
        blocking = json.dumps({
            "file_comments": [{
                "file": "a.py", "line_start": 1, "line_end": 1,
                "severity": "critical", "title": "t", "comment": "c",
                "code_snippet": "",
            }],
            "review_summary": {"summary": "Has a blocker.", "checklist": []},
        })
        result = _parse_review_json(blocking)
        assert result is not None
        assert result["review_summary"]["lgtm"] is False

    def test_missing_summary_still_rejected(self):
        """Normalization must NOT fabricate semantically meaningful fields:
        a review with no summary is genuinely incomplete and stays rejected."""
        raw = json.dumps({
            "file_comments": [],
            "review_summary": {"lgtm": True},
        })
        result = _parse_review_json(raw)
        assert result is None


# ---------------------------------------------------------------------------
# _format_review_as_markdown
# ---------------------------------------------------------------------------

class TestFormatReviewAsMarkdown:
    def test_formats_with_findings(self):
        md = _format_review_as_markdown(VALID_REVIEW_JSON, title="Fix auth")
        assert "## PR Review — Fix auth" in md
        assert "### 🔴 Blocking" in md
        assert "Missing validation" in md
        assert "`auth.py`" in md
        assert "L42" in md
        assert "<details>" in md
        assert "<summary>" in md
        assert "</details>" in md

    def test_summary_rendered_once(self):
        """Regression: the summary paragraph must appear exactly once.

        Previously _format_review_as_markdown emitted summary_data["summary"]
        both as the lead paragraph and again under a trailing "### Summary"
        heading, so posted reviews showed the identical text twice (see
        esphome/esphome#15346). It must now render only once.
        """
        summary_text = VALID_REVIEW_JSON["review_summary"]["summary"]
        md = _format_review_as_markdown(VALID_REVIEW_JSON, title="Fix auth")
        assert md.count(summary_text) == 1
        # The redundant trailing heading is gone.
        assert "### Summary" not in md

    def test_summary_preserves_paragraphs_and_bullets(self):
        """A structured summary (verdict + blank line + bullets) must survive
        formatting intact, so readers get skimmable output instead of one
        dense block. The formatter passes the summary through verbatim, so the
        blank-line break and bullet markers must appear in the rendered body.
        """
        data = {
            "file_comments": [],
            "review_summary": {
                "lgtm": False,
                "summary": "Needs work before merge.\n\n- Missing input validation\n- No test coverage",
                "checklist": [],
            },
        }
        md = _format_review_as_markdown(data)
        assert "Needs work before merge.\n\n- Missing input validation" in md
        assert "- No test coverage" in md

    def test_lgtm_review(self):
        md = _format_review_as_markdown(LGTM_REVIEW_JSON)
        assert "## PR Review" in md
        assert "### 🔴" not in md
        assert "### 🟡" not in md
        assert "### 🟢" not in md
        assert "Merge-ready" in md

    def test_all_severity_levels(self):
        data = {
            "file_comments": [
                {"file": "a.py", "line_start": 1, "line_end": 1,
                 "severity": "critical", "title": "Bug", "comment": "Fix it",
                 "code_snippet": ""},
                {"file": "b.py", "line_start": 10, "line_end": 15,
                 "severity": "warning", "title": "Perf", "comment": "Slow",
                 "code_snippet": "for x in y"},
                {"file": "c.py", "line_start": 0, "line_end": 0,
                 "severity": "suggestion", "title": "Style", "comment": "Rename",
                 "code_snippet": ""},
            ],
            "review_summary": {"lgtm": False, "summary": "Needs work.",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "### 🔴 Blocking" in md
        assert "### 🟡 Important" in md
        assert "### 🟢 Suggestions" in md
        assert "L10-15" in md  # multi-line range

    def test_checklist_rendering(self):
        data = {
            "file_comments": [],
            "review_summary": {
                "lgtm": True,
                "summary": "Good.",
                "checklist": [
                    {"item": "No secrets", "passed": True, "finding_ref": ""},
                    {"item": "Input validated", "passed": False, "finding_ref": "critical #1"},
                ],
            },
        }
        md = _format_review_as_markdown(data)
        assert "- [x] No secrets" in md
        # #N refs must use fullwidth ＃ (U+FF03) to prevent GitHub auto-linking
        assert "- [ ] Input validated \u2014 critical \uff031" in md
        assert "#1" not in md

    def test_checklist_finding_refs_escape_hash(self):
        """All #N cross-references in checklist use fullwidth ＃ to prevent GitHub auto-linking."""
        data = {
            "file_comments": [],
            "review_summary": {
                "lgtm": False,
                "summary": "Issues found.",
                "checklist": [
                    {"item": "Return type matches interface", "passed": False, "finding_ref": "warning #1"},
                    {"item": "Null check present", "passed": False, "finding_ref": "warning #2"},
                    {"item": "No duplicated validation", "passed": False, "finding_ref": "suggestion #1"},
                    {"item": "No secrets", "passed": True, "finding_ref": ""},
                ],
            },
        }
        md = _format_review_as_markdown(data)
        # Fullwidth ＃ must appear in refs
        assert "\uff031" in md
        assert "\uff032" in md
        # ASCII # must NOT appear after the em dash (in finding refs)
        checklist_lines = [l for l in md.splitlines() if l.startswith("- [")]
        for line in checklist_lines:
            if "\u2014" in line:  # em dash separates the ref
                ref_part = line.split("\u2014", 1)[1]
                assert "#" not in ref_part, f"ASCII # found in ref: {line!r}"

    def test_code_snippet_in_output(self):
        data = {
            "file_comments": [{
                "file": "x.py", "line_start": 5, "line_end": 5,
                "severity": "warning", "title": "Issue",
                "comment": "Problem here",
                "code_snippet": "x = eval(input())",
            }],
            "review_summary": {"lgtm": False, "summary": "Fix eval.",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "x = eval(input())" in md
        assert "```" in md


# ---------------------------------------------------------------------------
# _build_review_footer
# ---------------------------------------------------------------------------

class TestBuildReviewFooter:
    def test_no_provider_no_model(self):
        result = _build_review_footer()
        assert result == "_Automated review by [Kōan](https://koan.anantys.com)_"

    def test_provider_only(self):
        result = _build_review_footer(provider_name="claude")
        assert "[Kōan](https://koan.anantys.com)" in result
        assert "Claude" in result

    def test_model_only(self):
        result = _build_review_footer(model="claude-opus-4-6")
        assert "model claude-opus-4-6" in result

    def test_provider_and_model(self):
        result = _build_review_footer(provider_name="claude", model="claude-sonnet-4-6")
        assert "[Kōan](https://koan.anantys.com)" in result
        assert "Claude" in result
        assert "model claude-sonnet-4-6" in result
        assert " · " in result

    def test_provider_capitalized(self):
        result = _build_review_footer(provider_name="copilot", model="gpt-4o")
        assert "Copilot" in result


# ---------------------------------------------------------------------------
# _post_review_comment
# ---------------------------------------------------------------------------

class TestPostReviewComment:
    @patch("app.review_runner.run_gh")
    def test_posts_comment(self, mock_gh):
        """Posts review as PR comment via gh CLI."""
        success, error = _post_review_comment("owner", "repo", "42", "LGTM")
        assert success is True
        assert error == ""
        mock_gh.assert_called_once()
        call_args = mock_gh.call_args
        assert "pr" in call_args[0]
        assert "comment" in call_args[0]
        assert "42" in call_args[0]
        # Body should contain the review text
        body_arg = call_args[1].get("body") or call_args[0][-1]
        # The body is passed via --body flag
        assert any("LGTM" in str(a) for a in call_args[0])

    @patch("app.review_runner.run_gh", side_effect=RuntimeError("API error"))
    def test_returns_false_on_error(self, mock_gh):
        """Returns (False, error_detail) when gh CLI fails."""
        success, error = _post_review_comment("owner", "repo", "42", "review")
        assert success is False
        assert "API error" in error

    @patch("app.review_runner.run_gh")
    def test_truncates_long_review(self, mock_gh):
        """Reviews longer than 60000 chars are truncated."""
        long_review = "x" * 70000
        _post_review_comment("owner", "repo", "42", long_review)
        call_args = mock_gh.call_args[0]
        body = [a for a in call_args if isinstance(a, str) and len(a) > 1000][0]
        assert len(body) < 65000
        assert "truncated" in body.lower()

    @patch("app.review_runner.run_gh")
    def test_no_double_heading_for_structured_review(self, mock_gh):
        """Reviews starting with ## don't get an extra ## Code Review header."""
        from app.review_markers import SUMMARY_TAG
        review = "## PR Review — Fix auth\n\nLGTM"
        _post_review_comment("owner", "repo", "42", review)
        call_args = mock_gh.call_args[0]
        body = [a for a in call_args if isinstance(a, str) and "LGTM" in a][0]
        assert "## Code Review" not in body
        assert "## PR Review" in body
        # SUMMARY_TAG is prepended to enable idempotent upserts
        assert SUMMARY_TAG in body

    @patch("app.review_runner.run_gh")
    def test_legacy_review_gets_heading(self, mock_gh):
        """Reviews without ## heading get wrapped with ## Code Review."""
        review = "Looks good, no issues found."
        _post_review_comment("owner", "repo", "42", review)
        call_args = mock_gh.call_args[0]
        body = [a for a in call_args if isinstance(a, str) and "Looks good" in a][0]
        assert "## Code Review" in body

    @patch("app.review_runner.run_gh")
    def test_footer_contains_provider_and_model(self, mock_gh):
        """Footer includes provider name and model when provided."""
        _post_review_comment(
            "owner", "repo", "42", "LGTM",
            provider_name="claude", model="claude-opus-4-6",
        )
        body = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "LGTM" in a][0]
        assert "[Kōan](https://koan.anantys.com)" in body
        assert "Claude" in body
        assert "model claude-opus-4-6" in body

    @patch("app.review_runner.run_gh")
    def test_footer_shows_head_sha(self, mock_gh):
        """Footer displays HEAD=<sha> when commit_shas provided."""
        _post_review_comment(
            "owner", "repo", "42", "LGTM",
            commit_shas=["abc123full", "def456full"],
        )
        body = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "LGTM" in a][0]
        assert "`HEAD=def456f`" in body

    @patch("app.review_runner.run_gh")
    def test_footer_no_head_sha_without_commits(self, mock_gh):
        """Footer omits HEAD= when no commit_shas provided."""
        _post_review_comment("owner", "repo", "42", "LGTM")
        body = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "LGTM" in a][0]
        assert "HEAD=" not in body

    @patch("app.review_runner.run_gh")
    def test_commits_hidden_in_single_html_comment(self, mock_gh):
        """Commit SHAs are stored in a single HTML comment (invisible on GitHub)."""
        _post_review_comment(
            "owner", "repo", "42", "LGTM",
            commit_shas=["abc123", "def456"],
        )
        body = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "LGTM" in a][0]
        assert "<!-- koan-commits\nabc123\ndef456\n-->" in body


# ---------------------------------------------------------------------------
# run_review (integration, mocked externals)
# ---------------------------------------------------------------------------

class TestRunReview:
    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_full_pipeline_with_json(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """Full review pipeline with JSON output: fetch -> claude -> parse -> post."""
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")
        mock_notify = MagicMock()

        success, summary, review_data = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert "42" in summary
        assert review_data is not None
        assert review_data["review_summary"]["lgtm"] is True
        mock_fetch.assert_called_once_with("owner", "repo", "42", "/tmp/project")
        mock_claude.assert_called_once()
        mock_gh.assert_called_once()  # post comment
        assert mock_notify.call_count >= 2

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_fallback_to_markdown_on_invalid_json(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """Falls back to regex extraction when JSON parsing fails twice."""
        mock_fetch.return_value = pr_context
        # Both attempts return markdown instead of JSON
        mock_claude.return_value = (
            "## PR Review — Fix auth bypass\n\n"
            "Solid fix. No issues found.\n\n---\n\n"
            "### Summary\n\nMerge-ready.",
            "",
        )
        mock_notify = MagicMock()

        success, summary, review_data = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert review_data is None  # fallback was used
        # Claude called twice (initial + retry)
        assert mock_claude.call_count == 2
        mock_gh.assert_called_once()

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner._run_error_hunter", return_value="")
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_unparseable_output_posts_placeholder_not_raw(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        _mock_hunter, _mock_shas, pr_context, review_skill_dir,
    ):
        """Guardrail: when neither attempt yields parseable/structured output,
        post a short placeholder (never the raw narration) and alert a human."""
        mock_fetch.return_value = pr_context
        raw_junk = (
            "Sorry — I was unable to produce a structured review here; "
            "these are some loose notes about the diff."
        )
        mock_claude.return_value = (raw_junk, "")
        mock_notify = MagicMock()

        success, summary, review_data = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert review_data is None
        assert mock_claude.call_count == 2  # initial + retry
        mock_gh.assert_called_once()

        posted_body = mock_gh.call_args.args[-1]
        assert "could not be formatted" in posted_body
        assert "loose notes about the diff" not in posted_body  # raw never posted

        alerts = [str(c.args[0]) for c in mock_notify.call_args_list if c.args]
        assert any("couldn't be parsed" in a for a in alerts)

    @patch("app.review_runner._reflect_findings", side_effect=lambda findings, *a, **kw: findings)
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_retry_succeeds_on_second_attempt(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_reflect,
        pr_context, review_skill_dir,
    ):
        """Retry produces valid JSON on second attempt."""
        mock_fetch.return_value = pr_context
        # First call returns markdown, second returns JSON
        mock_claude.side_effect = [
            ("Not JSON at all", ""),
            (json.dumps(VALID_REVIEW_JSON), ""),
        ]
        mock_notify = MagicMock()

        success, summary, review_data = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert review_data is not None
        assert review_data["review_summary"]["lgtm"] is False
        assert mock_claude.call_count == 2

    @patch("app.review_runner.fetch_pr_context")
    def test_fetch_failure(self, mock_fetch, pr_context):
        """Handles PR context fetch failure."""
        mock_fetch.side_effect = RuntimeError("API down")
        mock_notify = MagicMock()

        success, summary, _rd = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
        )

        assert success is False
        assert "Failed to fetch" in summary

    @patch("app.review_runner.fetch_pr_context")
    def test_empty_diff(self, mock_fetch, pr_context):
        """Returns a successful no-op when PR has no diff."""
        pr_context["diff"] = ""
        mock_fetch.return_value = pr_context
        mock_notify = MagicMock()

        success, summary, _rd = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
        )

        assert success is True
        assert "no diff" in summary

    @patch("app.review_runner.fetch_pr_context")
    def test_diff_unavailable(self, mock_fetch, pr_context):
        """Diff fetch failures are not mislabeled as no-change PRs."""
        pr_context["diff"] = ""
        pr_context["diff_error"] = "HTTP 406: diff exceeded maximum"
        mock_fetch.return_value = pr_context
        mock_notify = MagicMock()

        success, summary, _rd = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
        )

        assert success is False
        assert "diff unavailable" in summary
        assert "no diff" not in summary

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_claude_empty_output(
        self, mock_fetch, mock_claude, mock_repliable,
        pr_context, review_skill_dir,
    ):
        """Returns failure when Claude produces no output."""
        mock_fetch.return_value = pr_context
        mock_claude.return_value = ("", "Timeout (300s)")
        mock_notify = MagicMock()

        success, summary, _rd = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is False
        assert "failed" in summary.lower()
        assert "Timeout" in summary

    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_claude_failure_without_error_detail(
        self, mock_fetch, mock_claude, pr_context, review_skill_dir,
    ):
        """Failure without error detail still reports cleanly."""
        mock_fetch.return_value = pr_context
        mock_claude.return_value = ("", "")
        mock_notify = MagicMock()

        success, summary, _rd = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is False
        assert "failed" in summary.lower()
        # No error detail — message should not contain "()"
        assert "()" not in summary

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh", side_effect=RuntimeError("post fail"))
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_comment_post_failure(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        pr_context, review_skill_dir,
    ):
        """Handles comment posting failure."""
        mock_fetch.return_value = pr_context
        mock_claude.return_value = ("## PR Review — Fix auth bypass\n\nGood code", "")
        mock_notify = MagicMock()

        success, summary, _rd = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is False
        assert "failed to post" in summary.lower()


# ---------------------------------------------------------------------------
# _run_claude_review
# ---------------------------------------------------------------------------

class TestRunClaudeReview:
    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config", return_value={"review_mode": "review-model", "mission": "mission-model"})
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_success_returns_output_and_empty_error(
        self, mock_max_turns, mock_models, mock_run,
    ):
        """On success, returns (output, empty error)."""
        from app.review_runner import _run_claude_review

        mock_run.return_value = "review text"
        output, error = _run_claude_review("prompt", "/tmp/project")
        assert output == "review text"
        assert error == ""

    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config", return_value={"review_mode": "review-model", "mission": "mission-model"})
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_failure_returns_error_detail(
        self, mock_max_turns, mock_models, mock_run,
    ):
        """On failure, returns empty output and error detail."""
        from app.review_runner import _run_claude_review

        mock_run.side_effect = RuntimeError("Timeout (300s)")
        output, error = _run_claude_review("prompt", "/tmp/project")
        assert output == ""
        assert "Timeout" in error

    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config", return_value={"review_mode": "review-model", "mission": "mission-model"})
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_failure_logs_to_stderr(
        self, mock_max_turns, mock_models, mock_run, capsys,
    ):
        """Failure is logged to stderr for diagnostics."""
        from app.review_runner import _run_claude_review

        mock_run.side_effect = RuntimeError("Exit code 1: model error")
        _run_claude_review("prompt", "/tmp/project")
        captured = capsys.readouterr()
        assert "Provider review failed" in captured.err
        assert "Exit code 1" in captured.err

    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config", return_value={"review_mode": "review-model", "mission": "mission-model"})
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_default_timeout_is_600(
        self, mock_max_turns, mock_models, mock_run,
    ):
        """Default timeout increased from 300 to 600 for large PRs."""
        from app.review_runner import _run_claude_review

        mock_run.return_value = "ok"
        _run_claude_review("prompt", "/tmp/project")
        # Verify run_command_streaming was called with timeout=600
        _, kwargs = mock_run.call_args
        assert kwargs.get("timeout") == 600

    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config")
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_model_override_is_forwarded(self, mock_max_turns, mock_models, mock_run):
        from app.review_runner import _run_claude_review

        mock_run.return_value = "ok"
        _run_claude_review("prompt", "/tmp/project", model="gpt-5.4-mini")
        _, kwargs = mock_run.call_args
        assert kwargs.get("model") == "gpt-5.4-mini"
        mock_models.assert_not_called()


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------

class TestMainCli:
    @patch("app.review_runner.run_review")
    def test_valid_pr_url(self, mock_run):
        """CLI parses PR URL and calls run_review."""
        from app.review_runner import main

        mock_run.return_value = (True, "Review posted.", None)
        exit_code = main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/tmp/project",
        ])

        assert exit_code == 0
        mock_run.assert_called_once_with(
            "owner", "repo", "42", "/tmp/project",
            skill_dir=Path(__file__).resolve().parent.parent / "skills" / "core" / "review",
            architecture=False,
            plan_url=None,
            project_name=None,
            errors=False,
            comments=False,
        )

    @patch("app.review_runner.run_review")
    def test_failure_returns_1(self, mock_run):
        """CLI returns exit code 1 on review failure."""
        from app.review_runner import main

        mock_run.return_value = (False, "Claude failed.", None)
        exit_code = main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/tmp/project",
        ])

        assert exit_code == 1

    def test_invalid_url(self):
        """CLI returns exit code 1 for non-PR URLs."""
        from app.review_runner import main

        exit_code = main([
            "https://github.com/owner/repo/issues/42",
            "--project-path", "/tmp/project",
        ])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Skill dispatch integration
# ---------------------------------------------------------------------------

class TestArchitectureFlag:
    """Tests for the --architecture flag."""

    def test_cli_parses_architecture_flag(self):
        """CLI parses --architecture flag."""
        from app.review_runner import main

        with patch("app.review_runner.run_review") as mock_run:
            mock_run.return_value = (True, "Review posted.", None)
            main([
                "https://github.com/owner/repo/pull/42",
                "--project-path", "/tmp/project",
                "--architecture",
            ])

            call_kwargs = mock_run.call_args
            assert call_kwargs[1].get("architecture") is True or (
                len(call_kwargs[0]) >= 6 and call_kwargs[0][5] is True
            )

    def test_cli_default_no_architecture(self):
        """CLI defaults to no architecture flag."""
        from app.review_runner import main

        with patch("app.review_runner.run_review") as mock_run:
            mock_run.return_value = (True, "Review posted.", None)
            main([
                "https://github.com/owner/repo/pull/42",
                "--project-path", "/tmp/project",
            ])

            _, kwargs = mock_run.call_args
            assert kwargs.get("architecture") is False or "architecture" not in kwargs

    def test_build_prompt_architecture_selects_correct_template(
        self, pr_context, tmp_path,
    ):
        """build_review_prompt with architecture=True loads review-architecture template."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review-architecture.md").write_text(
            "ARCH REVIEW: {TITLE}\nAuthor: {AUTHOR}\nBranch: {BRANCH} -> {BASE}\n"
            "Body: {BODY}\nDiff: {DIFF}\n"
            "Reviews: {REVIEWS}\nComments: {REVIEW_COMMENTS}\n"
            "Issue: {ISSUE_COMMENTS}\n"
        )

        prompt = build_review_prompt(
            pr_context, skill_dir=tmp_path, architecture=True,
        )
        assert "ARCH REVIEW:" in prompt
        assert "Fix auth bypass" in prompt

    def test_build_prompt_default_selects_review_template(
        self, pr_context, review_skill_dir,
    ):
        """build_review_prompt without architecture uses standard review template."""
        prompt = build_review_prompt(
            pr_context, skill_dir=review_skill_dir, architecture=False,
        )
        assert "Review PR:" in prompt
        assert "{TITLE}" not in prompt

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_run_review_passes_architecture_to_prompt(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, tmp_path,
    ):
        """run_review with architecture=True uses architecture prompt."""
        # Set up skill dir with both prompts
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review.md").write_text(
            "STANDARD: {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} "
            "{REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}"
        )
        (prompts_dir / "review-architecture.md").write_text(
            "ARCHITECTURE: {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} "
            "{REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}"
        )

        mock_fetch.return_value = pr_context
        mock_claude.return_value = ("## PR Review — Fix auth bypass\n\nGood", "")
        mock_notify = MagicMock()

        run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=tmp_path,
            architecture=True,
        )

        # Verify the architecture prompt was passed to Claude
        prompt_arg = mock_claude.call_args[0][0]
        assert "ARCHITECTURE:" in prompt_arg
        assert "STANDARD:" not in prompt_arg


class TestSkillDispatchIntegration:
    """Verify /review is properly wired in skill_dispatch.py."""

    def test_review_in_skill_runners(self):
        """'review' is registered in _SKILL_RUNNERS."""
        from app.skill_dispatch import _SKILL_RUNNERS
        assert "review" in _SKILL_RUNNERS
        assert _SKILL_RUNNERS["review"] == "app.review_runner"

    def test_review_mission_is_skill(self):
        """is_skill_mission recognizes /review missions."""
        from app.skill_dispatch import is_skill_mission
        assert is_skill_mission("/review https://github.com/o/r/pull/1")

    def test_review_parse(self):
        """parse_skill_mission extracts review command."""
        from app.skill_dispatch import parse_skill_mission
        project, cmd, args = parse_skill_mission(
            "[project:koan] /review https://github.com/o/r/pull/1"
        )
        assert project == "koan"
        assert cmd == "review"
        assert "pull/1" in args

    def test_review_validate_args_valid(self):
        """validate_skill_args accepts PR URL for /review."""
        from app.skill_dispatch import validate_skill_args
        result = validate_skill_args(
            "review", "https://github.com/o/r/pull/1"
        )
        assert result is None

    def test_review_validate_args_invalid(self):
        """validate_skill_args rejects non-PR URLs for /review."""
        from app.skill_dispatch import validate_skill_args
        result = validate_skill_args("review", "no url here")
        assert result is not None
        assert "PR URL" in result

    @patch("app.skill_dispatch.is_known_project", return_value=True)
    def test_dispatch_builds_command(self, mock_known):
        """dispatch_skill_mission builds a command for /review."""
        from app.skill_dispatch import dispatch_skill_mission
        result = dispatch_skill_mission(
            mission_text="/review https://github.com/o/r/pull/1",
            project_name="koan",
            project_path="/tmp/project",
            koan_root="/tmp/koan",
            instance_dir="/tmp/instance",
        )
        assert result is not None
        assert any("review_runner" in str(p) for p in result)
        assert "/tmp/project" in result

    @patch("app.skill_dispatch.is_known_project", return_value=True)
    def test_dispatch_passes_architecture_flag(self, mock_known):
        """dispatch_skill_mission passes --architecture when present."""
        from app.skill_dispatch import dispatch_skill_mission
        result = dispatch_skill_mission(
            mission_text="/review https://github.com/o/r/pull/1 --architecture",
            project_name="koan",
            project_path="/tmp/project",
            koan_root="/tmp/koan",
            instance_dir="/tmp/instance",
        )
        assert result is not None
        assert "--architecture" in result

    @patch("app.skill_dispatch.is_known_project", return_value=True)
    def test_dispatch_no_architecture_by_default(self, mock_known):
        """dispatch_skill_mission does not include --architecture by default."""
        from app.skill_dispatch import dispatch_skill_mission
        result = dispatch_skill_mission(
            mission_text="/review https://github.com/o/r/pull/1",
            project_name="koan",
            project_path="/tmp/project",
            koan_root="/tmp/koan",
            instance_dir="/tmp/instance",
        )
        assert result is not None
        assert "--architecture" not in result

    @patch("app.skill_dispatch.is_known_project", return_value=True)
    def test_dispatch_architecture_before_url(self, mock_known):
        """dispatch_skill_mission handles --architecture before URL."""
        from app.skill_dispatch import dispatch_skill_mission
        result = dispatch_skill_mission(
            mission_text="/review --architecture https://github.com/o/r/pull/1",
            project_name="koan",
            project_path="/tmp/project",
            koan_root="/tmp/koan",
            instance_dir="/tmp/instance",
        )
        assert result is not None
        assert "--architecture" in result
        assert any("pull/1" in str(p) for p in result)


# ---------------------------------------------------------------------------
# fetch_repliable_comments
# ---------------------------------------------------------------------------

class TestFetchRepliableComments:
    @patch("app.review_runner.run_gh")
    def test_fetches_review_and_issue_comments(self, mock_gh):
        """Fetches both review comments and issue comments with IDs."""
        review_json = json.dumps({
            "id": 100, "user": "alice", "body": "Why this approach?",
            "path": "auth.py", "line": 42, "user_type": "User",
        })
        issue_json = json.dumps({
            "id": 200, "user": "bob", "body": "Looks good overall",
            "user_type": "User",
        })
        mock_gh.side_effect = [review_json, issue_json]

        comments = fetch_repliable_comments("owner", "repo", "42")

        assert len(comments) == 2
        assert comments[0]["id"] == 100
        assert comments[0]["type"] == "review_comment"
        assert comments[0]["user"] == "alice"
        assert comments[0]["path"] == "auth.py"
        assert comments[1]["id"] == 200
        assert comments[1]["type"] == "issue_comment"

    @patch("app.review_runner.run_gh")
    def test_skips_bot_comments(self, mock_gh):
        """Bot comments are excluded from repliable list."""
        review_json = json.dumps({
            "id": 100, "user": "github-actions", "body": "CI passed",
            "path": "x.py", "line": 1, "user_type": "Bot",
        })
        mock_gh.side_effect = [review_json, ""]

        comments = fetch_repliable_comments("owner", "repo", "42")

        assert len(comments) == 0

    @patch("app.review_runner.run_gh", side_effect=RuntimeError("API error"))
    def test_handles_api_errors(self, mock_gh):
        """Returns empty list on API errors."""
        comments = fetch_repliable_comments("owner", "repo", "42")
        assert comments == []

    @patch("app.review_runner.run_gh")
    def test_empty_response(self, mock_gh):
        """Handles empty responses gracefully."""
        mock_gh.return_value = ""
        comments = fetch_repliable_comments("owner", "repo", "42")
        assert comments == []


# ---------------------------------------------------------------------------
# _format_repliable_comments
# ---------------------------------------------------------------------------

class TestFormatRepliableComments:
    def test_no_comments(self):
        result = _format_repliable_comments([])
        assert "No comments" in result

    def test_formats_review_comment(self):
        comments = [{
            "id": 100, "type": "review_comment", "user": "alice",
            "body": "Why this approach?", "path": "auth.py", "line": 42,
        }]
        result = _format_repliable_comments(comments)
        assert "[id=100]" in result
        assert "@alice" in result
        assert "auth.py:42" in result
        assert "Why this approach?" in result

    def test_formats_issue_comment(self):
        comments = [{
            "id": 200, "type": "issue_comment", "user": "bob",
            "body": "Overall this looks good",
        }]
        result = _format_repliable_comments(comments)
        assert "[id=200]" in result
        assert "@bob" in result
        assert "Overall this looks good" in result

    def test_truncates_long_bodies(self):
        comments = [{
            "id": 300, "type": "issue_comment", "user": "carol",
            "body": "x" * 600,
        }]
        result = _format_repliable_comments(comments)
        assert "..." in result
        assert len(result) < 700


# ---------------------------------------------------------------------------
# _post_comment_replies
# ---------------------------------------------------------------------------

class TestPostCommentReplies:
    @patch("app.review_runner.run_gh")
    def test_posts_review_comment_reply(self, mock_gh):
        """Replies to review comments via the pull request comment API."""
        replies = [{"comment_id": 100, "reply": "Good question — see L42."}]
        repliable = [{"id": 100, "type": "review_comment", "user": "alice", "body": "Why?"}]

        count = _post_comment_replies("owner", "repo", "42", replies, repliable)

        assert count == 1
        call_args = mock_gh.call_args[0]
        assert "repos/owner/repo/pulls/42/comments" in call_args[1]
        assert "-X" in call_args
        assert "POST" in call_args

    @patch("app.review_runner.run_gh")
    def test_posts_issue_comment_reply(self, mock_gh):
        """Replies to issue comments via gh pr comment with quote."""
        replies = [{"comment_id": 200, "reply": "Thanks for the feedback."}]
        repliable = [{"id": 200, "type": "issue_comment", "user": "bob", "body": "Nice work"}]

        count = _post_comment_replies("owner", "repo", "42", replies, repliable)

        assert count == 1
        call_args = mock_gh.call_args[0]
        assert "pr" in call_args
        assert "comment" in call_args
        # Body should contain quote of original
        body = [a for a in call_args if isinstance(a, str) and "@bob" in a][0]
        assert "> @bob:" in body

    def test_empty_replies(self):
        """No-op when replies list is empty."""
        count = _post_comment_replies("owner", "repo", "42", [], [])
        assert count == 0

    @patch("app.review_runner.run_gh")
    def test_skips_unknown_comment_id(self, mock_gh):
        """Skips replies targeting non-existent comment IDs."""
        replies = [{"comment_id": 999, "reply": "Reply to nothing"}]
        repliable = [{"id": 100, "type": "issue_comment", "user": "alice", "body": "Hello"}]

        count = _post_comment_replies("owner", "repo", "42", replies, repliable)

        assert count == 0
        mock_gh.assert_not_called()

    @patch("app.review_runner.run_gh", side_effect=RuntimeError("API error"))
    def test_handles_post_failure(self, mock_gh):
        """Continues posting other replies when one fails."""
        replies = [{"comment_id": 100, "reply": "Reply"}]
        repliable = [{"id": 100, "type": "issue_comment", "user": "a", "body": "b"}]

        count = _post_comment_replies("owner", "repo", "42", replies, repliable)

        assert count == 0

    @patch("app.review_runner.run_gh")
    def test_skips_empty_reply(self, mock_gh):
        """Skips replies with empty text."""
        replies = [{"comment_id": 100, "reply": ""}]
        repliable = [{"id": 100, "type": "issue_comment", "user": "a", "body": "b"}]

        count = _post_comment_replies("owner", "repo", "42", replies, repliable)

        assert count == 0
        mock_gh.assert_not_called()


# ---------------------------------------------------------------------------
# run_review with comment replies
# ---------------------------------------------------------------------------

class TestRunReviewWithReplies:
    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments")
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_posts_replies_when_present(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """Posts replies to user comments when review includes comment_replies."""
        mock_fetch.return_value = pr_context
        review_with_replies = {
            **LGTM_REVIEW_JSON,
            "comment_replies": [
                {"comment_id": 100, "reply": "Good question — the reason is X."},
            ],
        }
        mock_claude.return_value = (json.dumps(review_with_replies), "")
        mock_repliable.return_value = [
            {"id": 100, "type": "review_comment", "user": "alice", "body": "Why?"},
        ]
        mock_notify = MagicMock()

        success, summary, review_data = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert "Replied to 1 comment" in summary
        # run_gh called: 1 for post_review_comment + 1 for reply
        assert mock_gh.call_count == 2

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_no_replies_when_no_repliable_comments(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """No reply posting when there are no repliable comments."""
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")
        mock_notify = MagicMock()

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert "Replied" not in summary
        mock_gh.assert_called_once()  # Only the review comment post


# ---------------------------------------------------------------------------
# Plan alignment — _detect_plan_url
# ---------------------------------------------------------------------------

class TestDetectPlanUrl:
    def test_finds_issue_url_in_body(self):
        """Extracts the first GitHub issue URL from a PR body."""
        body = "Implements https://github.com/owner/repo/issues/42 as requested."
        result = _detect_plan_url(body)
        assert result == "https://github.com/owner/repo/issues/42"

    def test_returns_none_when_no_issue_url(self):
        """Returns None when the PR body has no issue URL."""
        body = "This PR fixes a bug. No linked issue."
        assert _detect_plan_url(body) is None

    def test_ignores_pr_urls(self):
        """PR URLs (/pull/) are not matched — only issue URLs."""
        body = "Closes https://github.com/owner/repo/pull/10 and updates docs."
        assert _detect_plan_url(body) is None

    def test_returns_first_issue_url_when_multiple(self):
        """Returns the first issue URL when multiple are present."""
        body = (
            "From https://github.com/owner/repo/issues/10 "
            "and https://github.com/owner/repo/issues/20"
        )
        result = _detect_plan_url(body)
        assert result == "https://github.com/owner/repo/issues/10"

    def test_empty_body(self):
        """Empty PR body returns None."""
        assert _detect_plan_url("") is None

    def test_closes_shorthand_not_matched(self):
        """'Closes #42' shorthand (no full URL) returns None."""
        body = "Closes #42."
        assert _detect_plan_url(body) is None

    def test_issue_url_in_multiline_body(self):
        """Finds issue URL in a multi-line PR body."""
        body = (
            "## Summary\n\n"
            "This PR implements the plan.\n\n"
            "Closes https://github.com/acme/app/issues/99\n\n"
            "## Changes\n\n- Added feature\n"
        )
        result = _detect_plan_url(body)
        assert result == "https://github.com/acme/app/issues/99"


# ---------------------------------------------------------------------------
# Plan alignment — _fetch_plan_body
# ---------------------------------------------------------------------------

class TestFetchPlanBody:
    @patch("app.review_runner.run_gh")
    def test_returns_empty_when_no_plan_label(self, mock_gh):
        """Returns empty string if the issue has no 'plan' label."""
        mock_gh.return_value = json.dumps({
            "body": "This is a regular issue.",
            "labels": [{"name": "bug"}, {"name": "enhancement"}],
        })
        result = _fetch_plan_body("owner", "repo", "42")
        assert result == ""

    @patch("app.review_runner.run_gh")
    def test_returns_body_when_plan_label(self, mock_gh):
        """Returns issue body when 'plan' label is present."""
        mock_gh.side_effect = [
            json.dumps({
                "body": "## Summary\n\nPlan content here.",
                "labels": [{"name": "plan"}],
            }),
            "",  # No comments
        ]
        result = _fetch_plan_body("owner", "repo", "42")
        assert result == "## Summary\n\nPlan content here."

    @patch("app.review_runner.run_gh")
    def test_strips_plan_footer(self, mock_gh):
        """Strips the Kōan /plan footer from the returned body."""
        mock_gh.side_effect = [
            json.dumps({
                "body": "## Summary\n\nPlan text.\n---\n*Generated by Kōan /plan — iteration 1*",
                "labels": [{"name": "plan"}],
            }),
            "",  # No comments
        ]
        result = _fetch_plan_body("owner", "repo", "42")
        assert result == "## Summary\n\nPlan text."
        assert "Generated by Kōan" not in result

    @patch("app.review_runner.run_gh")
    def test_uses_latest_comment_with_implementation_phases(self, mock_gh):
        """Uses the last comment body if it contains '### Implementation Phases'."""
        comment_line = json.dumps({"body": "### Implementation Phases\n\nUpdated plan."})
        mock_gh.side_effect = [
            json.dumps({
                "body": "Original plan body.",
                "labels": [{"name": "plan"}],
            }),
            comment_line,
        ]
        result = _fetch_plan_body("owner", "repo", "42")
        assert "Updated plan." in result
        assert "Original plan body." not in result

    @patch("app.review_runner.run_gh")
    def test_returns_empty_on_fetch_error(self, mock_gh):
        """Returns empty string if the GitHub API call fails."""
        mock_gh.side_effect = RuntimeError("API error")
        result = _fetch_plan_body("owner", "repo", "42")
        assert result == ""

    @patch("app.review_runner.run_gh")
    def test_returns_empty_on_json_error(self, mock_gh):
        """Returns empty string if the API response is not valid JSON."""
        mock_gh.return_value = "not json"
        result = _fetch_plan_body("owner", "repo", "42")
        assert result == ""


# ---------------------------------------------------------------------------
# Plan alignment — _truncate_plan
# ---------------------------------------------------------------------------

class TestTruncatePlan:
    def test_extracts_summary_section(self):
        """Extracts ## Summary section from the plan."""
        plan = (
            "## Background\n\nSome history.\n\n"
            "## Summary\n\nThis is the summary.\n\n"
            "## Next Steps\n\nFuture work."
        )
        result = _truncate_plan(plan)
        assert "This is the summary." in result

    def test_extracts_implementation_phases_section(self):
        """Extracts ### Implementation Phases section."""
        plan = (
            "## Summary\n\nBrief.\n\n"
            "### Implementation Phases\n\n#### Phase 1\nDo this.\n\n"
            "### Open Questions\n\nTBD."
        )
        result = _truncate_plan(plan)
        assert "Phase 1" in result

    def test_fallback_to_first_5000_chars(self):
        """Falls back to first 5000 chars when no sections are found."""
        plan = "x" * 10000
        result = _truncate_plan(plan)
        assert len(result) <= 5000 + 30  # 30 chars for the truncation note
        assert "...(plan truncated)" in result


# ---------------------------------------------------------------------------
# Plan alignment — build_review_prompt with plan
# ---------------------------------------------------------------------------

class TestBuildReviewPromptWithPlan:
    def test_selects_plan_prompt_when_plan_body_provided(self, pr_context, tmp_path):
        """Selects review-with-plan.md when plan_body is provided."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review.md").write_text("Standard review: {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")
        (prompts_dir / "review-with-plan.md").write_text("Plan review: {PLAN} {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")

        prompt = build_review_prompt(pr_context, skill_dir=tmp_path, plan_body="The plan content.")
        assert "Plan review:" in prompt
        assert "The plan content." in prompt

    def test_selects_standard_prompt_when_no_plan(self, pr_context, tmp_path):
        """Selects review.md when no plan_body is provided."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review.md").write_text("Standard review: {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")
        (prompts_dir / "review-with-plan.md").write_text("Plan review: {PLAN} {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")

        prompt = build_review_prompt(pr_context, skill_dir=tmp_path, plan_body=None)
        assert "Standard review:" in prompt
        assert "Plan review:" not in prompt

    def test_plan_overrides_architecture_flag(self, pr_context, tmp_path):
        """Plan alignment takes priority over --architecture flag."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review-architecture.md").write_text("Architecture review: {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")
        (prompts_dir / "review-with-plan.md").write_text("Plan review: {PLAN} {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {DIFF} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")

        prompt = build_review_prompt(
            pr_context, skill_dir=tmp_path,
            architecture=True, plan_body="The plan.",
        )
        assert "Plan review:" in prompt
        assert "Architecture review:" not in prompt

    def test_truncates_large_plan(self, pr_context, tmp_path):
        """Plan is truncated when combined plan+diff context exceeds 80K chars."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review-with-plan.md").write_text("Plan: {PLAN} Diff: {DIFF} Title: {TITLE} {AUTHOR} {BRANCH} {BASE} {BODY} {REVIEWS} {REVIEW_COMMENTS} {ISSUE_COMMENTS} {REPLIABLE_COMMENTS}")

        large_plan = "## Summary\n\nShort summary.\n\n" + "x" * 90_000
        pr_context["diff"] = "small diff"
        prompt = build_review_prompt(pr_context, skill_dir=tmp_path, plan_body=large_plan)
        # The plan should have been truncated — not 90K chars
        assert len(prompt) < 90_000 + 5000


# ---------------------------------------------------------------------------
# Plan alignment — _format_review_as_markdown with plan_alignment
# ---------------------------------------------------------------------------

class TestFormatReviewWithPlanAlignment:
    def test_renders_plan_alignment_section(self):
        """Renders ### Plan Alignment section when plan_alignment is present."""
        review_data = {
            **LGTM_REVIEW_JSON,
            "plan_alignment": {
                "requirements_met": ["Phase 1: _detect_plan_url added"],
                "requirements_missing": ["Phase 3: --plan-url flag missing"],
                "out_of_scope": [],
            },
        }
        result = _format_review_as_markdown(review_data)
        assert "### Plan Alignment" in result
        assert "✅ **Met**" in result
        assert "_detect_plan_url added" in result
        assert "❌ **Missing**" in result
        assert "--plan-url flag missing" in result

    def test_plan_alignment_before_severity_sections(self):
        """Plan alignment section appears before severity sections."""
        review_data = {
            **VALID_REVIEW_JSON,
            "plan_alignment": {
                "requirements_met": ["Req 1"],
                "requirements_missing": [],
                "out_of_scope": [],
            },
        }
        result = _format_review_as_markdown(review_data)
        plan_pos = result.find("### Plan Alignment")
        severity_pos = result.find("### 🔴 Blocking")
        assert plan_pos != -1
        assert severity_pos != -1
        assert plan_pos < severity_pos

    def test_no_plan_alignment_section_when_absent(self):
        """No Plan Alignment section when plan_alignment is not in data."""
        result = _format_review_as_markdown(LGTM_REVIEW_JSON)
        assert "### Plan Alignment" not in result

    def test_renders_out_of_scope_items(self):
        """Out-of-scope items are rendered when present."""
        review_data = {
            **LGTM_REVIEW_JSON,
            "plan_alignment": {
                "requirements_met": [],
                "requirements_missing": [],
                "out_of_scope": ["Extra helper added"],
            },
        }
        result = _format_review_as_markdown(review_data)
        assert "📋 **Out of scope**" in result
        assert "Extra helper added" in result


# ---------------------------------------------------------------------------
# Plan alignment — run_review auto-detection
# ---------------------------------------------------------------------------

class TestRunReviewPlanAlignment:
    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_auto_detects_plan_from_pr_body(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        plan_review_skill_dir,
    ):
        """Auto-detects plan URL from PR body and includes plan in prompt."""
        context = {
            "title": "Implement plan",
            "body": "Implements https://github.com/owner/repo/issues/10 per spec.",
            "branch": "feature/plan",
            "base": "main",
            "state": "OPEN",
            "author": "dev",
            "url": "https://github.com/owner/repo/pull/5",
            "diff": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n+x = 1",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }
        mock_fetch.return_value = context

        # First gh call: detect plan (gh api repos/.../issues/10)
        # Then comment post
        plan_issue = json.dumps({
            "body": "## Summary\n\nPlan here.",
            "labels": [{"name": "plan"}],
        })
        mock_gh.side_effect = [
            plan_issue,  # _fetch_plan_body: issue
            "",          # _fetch_plan_body: comments
            "posted",    # _post_review_comment
        ]
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        success, summary, _ = run_review(
            "owner", "repo", "5", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=plan_review_skill_dir,
        )
        assert success is True
        # Verify that plan fetching was attempted (gh api called for issues/10)
        assert mock_gh.call_count >= 2

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_no_plan_when_no_issue_in_body(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """No plan alignment when PR body has no linked issue URL."""
        pr_context["body"] = "Refactoring pass. No linked issue."
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        success, _, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )
        assert success is True
        # run_gh only called once: to post the review comment
        assert mock_gh.call_count == 1

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_explicit_plan_url_overrides_auto_detection(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, _mock_shas,
        pr_context, plan_review_skill_dir,
    ):
        """Explicit --plan-url fetches the specified issue, skipping auto-detect."""
        pr_context["body"] = "No issue URLs here."
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        plan_issue = json.dumps({
            "body": "## Summary\n\nExplicit plan.",
            "labels": [],  # No 'plan' label — explicit URLs skip label check
        })
        mock_gh.side_effect = [
            plan_issue,  # _resolve_plan_body: explicit issue fetch
            "",          # comments
            "posted",    # _post_review_comment
        ]

        success, _, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=plan_review_skill_dir,
            plan_url="https://github.com/owner/repo/issues/99",
        )
        assert success is True
        # Ensure plan issue was fetched
        first_call_args = mock_gh.call_args_list[0]
        assert "issues/99" in " ".join(str(a) for a in first_call_args[0])


# ---------------------------------------------------------------------------
# Plan alignment — CLI --plan-url flag
# ---------------------------------------------------------------------------

class TestPlanUrlCliFlag:
    @patch("app.review_runner.run_review")
    def test_cli_passes_plan_url(self, mock_run):
        """--plan-url is parsed and passed to run_review."""
        from app.review_runner import main

        mock_run.return_value = (True, "Review posted.", None)
        exit_code = main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/tmp/project",
            "--plan-url", "https://github.com/owner/repo/issues/10",
        ])

        assert exit_code == 0
        _, kwargs = mock_run.call_args
        assert kwargs["plan_url"] == "https://github.com/owner/repo/issues/10"

    @patch("app.review_runner.run_review")
    def test_cli_plan_url_defaults_to_none(self, mock_run):
        """--plan-url defaults to None when not provided."""
        from app.review_runner import main

        mock_run.return_value = (True, "Review posted.", None)
        main([
            "https://github.com/owner/repo/pull/42",
            "--project-path", "/tmp/project",
        ])

        _, kwargs = mock_run.call_args
        assert kwargs["plan_url"] is None


# ---------------------------------------------------------------------------
# Plan alignment — skill_dispatch --plan-url passthrough
# ---------------------------------------------------------------------------

class TestSkillDispatchPlanUrl:
    def test_passes_plan_url_to_review_cmd(self):
        """_build_review_cmd passes --plan-url when present in args."""
        from app.skill_dispatch import dispatch_skill_mission

        with patch("app.skill_dispatch.is_known_project", return_value=False):
            cmd = dispatch_skill_mission(
                "/review https://github.com/owner/repo/pull/5 "
                "--plan-url https://github.com/owner/repo/issues/3",
                project_name="myproject",
                project_path="/tmp/proj",
                koan_root="/tmp/koan",
                instance_dir="/tmp/instance",
            )

        assert cmd is not None
        assert "--plan-url" in cmd
        idx = cmd.index("--plan-url")
        assert cmd[idx + 1] == "https://github.com/owner/repo/issues/3"

    def test_no_plan_url_when_absent(self):
        """_build_review_cmd does not add --plan-url when not in args."""
        from app.skill_dispatch import dispatch_skill_mission

        with patch("app.skill_dispatch.is_known_project", return_value=False):
            cmd = dispatch_skill_mission(
                "/review https://github.com/owner/repo/pull/5",
                project_name="myproject",
                project_path="/tmp/proj",
                koan_root="/tmp/koan",
                instance_dir="/tmp/instance",
            )

        assert cmd is not None
        assert "--plan-url" not in cmd


# ---------------------------------------------------------------------------
# Concurrency: fetch_repliable_comments parallel parameter
# ---------------------------------------------------------------------------

class TestFetchRepliableCommentsParallel:
    """Tests for the parallel=False / parallel=True modes of fetch_repliable_comments."""

    @patch("app.review_runner.get_review_reply_config", return_value={"max_thread_depth": 5})
    @patch("app.review_runner._fetch_issue_comments")
    @patch("app.review_runner._fetch_inline_review_comments")
    def test_sequential_mode_calls_helpers_in_order(
        self, mock_inline, mock_issue, _cfg,
    ):
        """parallel=False calls the two fetch helpers sequentially."""
        mock_inline.return_value = [{"id": 1, "type": "review_comment"}]
        mock_issue.return_value = [{"id": 2, "type": "issue_comment"}]

        comments = fetch_repliable_comments("owner", "repo", "42", parallel=False)

        mock_inline.assert_called_once_with("owner/repo", "42", "", 5)
        mock_issue.assert_called_once_with("owner/repo", "42", "")
        assert len(comments) == 2
        assert comments[0]["id"] == 1
        assert comments[1]["id"] == 2

    @patch("app.review_runner.get_review_reply_config", return_value={"max_thread_depth": 5})
    @patch("app.review_runner._fetch_issue_comments")
    @patch("app.review_runner._fetch_inline_review_comments")
    def test_parallel_mode_collects_both_result_sets(
        self, mock_inline, mock_issue, _cfg,
    ):
        """parallel=True still collects results from both helpers."""
        mock_inline.return_value = [{"id": 10, "type": "review_comment"}]
        mock_issue.return_value = [{"id": 20, "type": "issue_comment"}]

        comments = fetch_repliable_comments("owner", "repo", "99", parallel=True)

        assert len(comments) == 2
        ids = {c["id"] for c in comments}
        assert ids == {10, 20}

    @patch("app.review_runner.get_review_reply_config", return_value={"max_thread_depth": 5})
    @patch("app.review_runner._fetch_issue_comments")
    @patch("app.review_runner._fetch_inline_review_comments")
    def test_empty_results_from_both_helpers(self, mock_inline, mock_issue, _cfg):
        """Returns empty list when both helpers return nothing."""
        mock_inline.return_value = []
        mock_issue.return_value = []

        comments = fetch_repliable_comments("owner", "repo", "1", parallel=False)
        assert comments == []


# ---------------------------------------------------------------------------
# Self-reply prevention: bot_username filtering
# ---------------------------------------------------------------------------

class TestSelfReplyPrevention:
    """Tests that bot's own comments are excluded when bot_username is provided."""

    @patch("app.review_runner.run_gh")
    def test_inline_comments_exclude_bot_username(self, mock_gh):
        """_fetch_inline_review_comments filters by bot_username."""
        from app.review_runner import _fetch_inline_review_comments

        mock_gh.return_value = "\n".join([
            json.dumps({"id": 1, "user": "human", "body": "fix this", "path": "a.py", "line": 10, "user_type": "User"}),
            json.dumps({"id": 2, "user": "koan-bot", "body": "done", "path": "a.py", "line": 10, "user_type": "User"}),
            json.dumps({"id": 3, "user": "other", "body": "lgtm", "path": "b.py", "line": 5, "user_type": "User"}),
        ])

        result = _fetch_inline_review_comments("owner/repo", "1", bot_username="koan-bot")
        assert len(result) == 2
        assert {c["id"] for c in result} == {1, 3}

    @patch("app.review_runner.run_gh")
    def test_issue_comments_exclude_bot_username(self, mock_gh):
        """_fetch_issue_comments filters by bot_username."""
        from app.review_runner import _fetch_issue_comments

        mock_gh.return_value = "\n".join([
            json.dumps({"id": 10, "user": "human", "body": "question", "user_type": "User"}),
            json.dumps({"id": 11, "user": "Koan-Bot", "body": "my reply", "user_type": "User"}),
        ])

        # Case-insensitive match
        result = _fetch_issue_comments("owner/repo", "1", bot_username="koan-bot")
        assert len(result) == 1
        assert result[0]["id"] == 10

    @patch("app.review_runner.run_gh")
    def test_no_filtering_when_bot_username_empty(self, mock_gh):
        """Without bot_username, no extra filtering occurs."""
        from app.review_runner import _fetch_inline_review_comments

        mock_gh.return_value = json.dumps(
            {"id": 1, "user": "koan-bot", "body": "x", "path": "a.py", "line": 1, "user_type": "User"}
        )

        result = _fetch_inline_review_comments("owner/repo", "1", bot_username="")
        assert len(result) == 1

    @patch("app.review_runner.get_review_reply_config", return_value={"max_thread_depth": 5})
    @patch("app.review_runner._fetch_issue_comments")
    @patch("app.review_runner._fetch_inline_review_comments")
    def test_fetch_repliable_passes_bot_username(self, mock_inline, mock_issue, _cfg):
        """fetch_repliable_comments forwards bot_username and max_thread_depth."""
        mock_inline.return_value = []
        mock_issue.return_value = []

        fetch_repliable_comments("o", "r", "1", parallel=False, bot_username="mybot")

        mock_inline.assert_called_once_with("o/r", "1", "mybot", 5)
        mock_issue.assert_called_once_with("o/r", "1", "mybot")


# ---------------------------------------------------------------------------
# Thread-aware self-reply guard: _is_bot_user, _filter_threads
# ---------------------------------------------------------------------------

class TestIsBotUser:
    """Tests for _is_bot_user helper."""

    def test_github_bot_type(self):
        from app.review_runner import _is_bot_user
        assert _is_bot_user({"user_type": "Bot", "user": "dependabot"}, "") is True

    def test_configured_bot_username(self):
        from app.review_runner import _is_bot_user
        assert _is_bot_user({"user_type": "User", "user": "Koan-Bot"}, "koan-bot") is True

    def test_human_user(self):
        from app.review_runner import _is_bot_user
        assert _is_bot_user({"user_type": "User", "user": "alice"}, "koan-bot") is False

    def test_no_bot_username_configured(self):
        from app.review_runner import _is_bot_user
        assert _is_bot_user({"user_type": "User", "user": "koan-bot"}, "") is False


class TestExcludeRepliedIssueComments:
    """Tests for _exclude_replied_issue_comments — flat issue comment dedup."""

    def test_excludes_comment_already_replied_to(self):
        from app.review_runner import _exclude_replied_issue_comments
        human = [
            {"id": 1, "user": "alice", "body": "Looks good overall"},
            {"id": 2, "user": "bob", "body": "Why this approach?"},
        ]
        bot = [
            {"body": "> @alice: Looks good overall\n\nThanks!"},
        ]
        result = _exclude_replied_issue_comments(human, bot)
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_keeps_unreplied_comments(self):
        from app.review_runner import _exclude_replied_issue_comments
        human = [
            {"id": 1, "user": "alice", "body": "New question"},
        ]
        bot = [
            {"body": "> @bob: Something else\n\nReply"},
        ]
        result = _exclude_replied_issue_comments(human, bot)
        assert len(result) == 1

    def test_handles_truncated_quotes(self):
        from app.review_runner import _exclude_replied_issue_comments
        long_body = "A" * 200
        human = [{"id": 1, "user": "alice", "body": long_body}]
        bot = [{"body": f"> @alice: {long_body[:100]}...\n\nReply"}]
        result = _exclude_replied_issue_comments(human, bot)
        assert len(result) == 0

    def test_exact_match_no_false_positive_on_prefix(self):
        """Non-truncated quotes require exact first-line match, not prefix."""
        from app.review_runner import _exclude_replied_issue_comments
        human = [
            {"id": 1, "user": "alice", "body": "Some text is wrong"},
            {"id": 2, "user": "alice", "body": "Some text is fine"},
        ]
        bot = [{"body": "> @alice: Some text is fine\n\nAgreed"}]
        result = _exclude_replied_issue_comments(human, bot)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_no_bot_replies_returns_all(self):
        from app.review_runner import _exclude_replied_issue_comments
        human = [{"id": 1, "user": "alice", "body": "Question"}]
        result = _exclude_replied_issue_comments(human, [])
        assert len(result) == 1

    def test_summary_comments_not_matched(self):
        """Bot summary comments (<!-- koan-summary -->) don't match as replies."""
        from app.review_runner import _exclude_replied_issue_comments
        human = [{"id": 1, "user": "alice", "body": "Good work"}]
        bot = [{"body": "<!-- koan-summary -->\n## PR Review\n\nLooks fine."}]
        result = _exclude_replied_issue_comments(human, bot)
        assert len(result) == 1

    def test_case_insensitive_user_match(self):
        from app.review_runner import _exclude_replied_issue_comments
        human = [{"id": 1, "user": "Alice", "body": "Question here"}]
        bot = [{"body": "> @alice: Question here\n\nAnswer"}]
        result = _exclude_replied_issue_comments(human, bot)
        assert len(result) == 0

    @patch("app.review_runner.run_gh")
    def test_fetch_issue_comments_excludes_replied(self, mock_gh):
        """_fetch_issue_comments uses _exclude_replied_issue_comments."""
        from app.review_runner import _fetch_issue_comments

        mock_gh.return_value = "\n".join([
            json.dumps({"id": 10, "user": "alice", "body": "Nice work", "user_type": "User"}),
            json.dumps({"id": 11, "user": "koan-bot", "body": "> @alice: Nice work\n\nThanks!", "user_type": "User"}),
            json.dumps({"id": 12, "user": "bob", "body": "New question", "user_type": "User"}),
        ])

        result = _fetch_issue_comments("owner/repo", "1", bot_username="koan-bot")
        assert len(result) == 1
        assert result[0]["user"] == "bob"


class TestFilterThreads:
    """Tests for _filter_threads — thread-aware self-reply prevention."""

    def test_excludes_thread_where_bot_is_last_poster(self):
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 1, "type": "review_comment", "user": "alice", "body": "fix this"},
        ]
        all_comments = [
            {"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None},
            {"id": 2, "user": "koan-bot", "user_type": "User", "in_reply_to_id": 1},
        ]
        result = _filter_threads(human_comments, all_comments, "koan-bot", 0)
        assert result == []

    def test_keeps_thread_where_human_replied_after_bot(self):
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 1, "type": "review_comment", "user": "alice", "body": "fix this"},
            {"id": 3, "type": "review_comment", "user": "bob", "body": "agreed",
             "in_reply_to_id": 1},
        ]
        all_comments = [
            {"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None},
            {"id": 2, "user": "koan-bot", "user_type": "User", "in_reply_to_id": 1},
            {"id": 3, "user": "bob", "user_type": "User", "in_reply_to_id": 1},
        ]
        result = _filter_threads(human_comments, all_comments, "koan-bot", 0)
        assert len(result) == 2

    def test_excludes_thread_at_max_depth(self):
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 1, "type": "review_comment", "user": "alice", "body": "fix"},
            {"id": 3, "type": "review_comment", "user": "alice", "body": "more",
             "in_reply_to_id": 1},
        ]
        all_comments = [
            {"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None},
            {"id": 2, "user": "koan-bot", "user_type": "User", "in_reply_to_id": 1},
            {"id": 3, "user": "alice", "user_type": "User", "in_reply_to_id": 1},
        ]
        result = _filter_threads(human_comments, all_comments, "koan-bot", 3)
        assert result == []

    def test_keeps_thread_under_max_depth(self):
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 1, "type": "review_comment", "user": "alice", "body": "fix"},
        ]
        all_comments = [
            {"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None},
            {"id": 2, "user": "koan-bot", "user_type": "User", "in_reply_to_id": 1},
        ]
        # Thread has 2 comments, max is 5 → keep. But bot is last → exclude.
        result = _filter_threads(human_comments, all_comments, "koan-bot", 5)
        assert result == []

        # Without bot_username check, thread is under max depth → keep
        result = _filter_threads(human_comments, all_comments, "", 5)
        assert len(result) == 1

    def test_no_filtering_when_both_disabled(self):
        from app.review_runner import _filter_threads

        human_comments = [{"id": 1, "type": "review_comment", "user": "alice", "body": "x"}]
        all_comments = [{"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None}]
        result = _filter_threads(human_comments, all_comments, "", 0)
        assert len(result) == 1

    def test_multiple_threads_filtered_independently(self):
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 1, "type": "review_comment", "user": "alice", "body": "thread 1"},
            {"id": 10, "type": "review_comment", "user": "bob", "body": "thread 2"},
        ]
        all_comments = [
            {"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None},
            {"id": 2, "user": "koan-bot", "user_type": "User", "in_reply_to_id": 1},
            {"id": 10, "user": "bob", "user_type": "User", "in_reply_to_id": None},
        ]
        # Thread 1: bot is last → excluded. Thread 2: only human → kept.
        result = _filter_threads(human_comments, all_comments, "koan-bot", 0)
        assert len(result) == 1
        assert result[0]["id"] == 10

    def test_reproduces_pr_1697_scenario(self):
        """Reproduces the exact scenario from PR #1697 where bot replied 4x."""
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 100, "type": "review_comment", "user": "teodesian",
             "body": "Review commentary..."},
        ]
        all_comments = [
            {"id": 100, "user": "teodesian", "user_type": "User", "in_reply_to_id": None},
            {"id": 201, "user": "Koan-Bot", "user_type": "User", "in_reply_to_id": 100},
            {"id": 202, "user": "Koan-Bot", "user_type": "User", "in_reply_to_id": 100},
            {"id": 203, "user": "Koan-Bot", "user_type": "User", "in_reply_to_id": 100},
            {"id": 204, "user": "Koan-Bot", "user_type": "User", "in_reply_to_id": 100},
        ]
        # Bot is last poster → excluded (even without max_depth)
        result = _filter_threads(human_comments, all_comments, "koan-bot", 0)
        assert result == []

        # Also excluded by max_depth=5 (5 comments in thread)
        result = _filter_threads(human_comments, all_comments, "", 5)
        assert result == []

    def test_github_bot_type_treated_as_bot(self):
        """GitHub Bots (user_type=Bot) are treated same as configured bot."""
        from app.review_runner import _filter_threads

        human_comments = [
            {"id": 1, "type": "review_comment", "user": "alice", "body": "x"},
        ]
        all_comments = [
            {"id": 1, "user": "alice", "user_type": "User", "in_reply_to_id": None},
            {"id": 2, "user": "copilot", "user_type": "Bot", "in_reply_to_id": 1},
        ]
        # copilot (Bot type) is last → not excluded (it's not our bot)
        # We only exclude when OUR bot is last poster
        result = _filter_threads(human_comments, all_comments, "koan-bot", 0)
        assert len(result) == 1


class TestFetchInlineWithThreadFilter:
    """Integration tests for _fetch_inline_review_comments with thread filtering."""

    @patch("app.review_runner.run_gh")
    def test_bot_last_reply_excluded(self, mock_gh):
        from app.review_runner import _fetch_inline_review_comments

        mock_gh.return_value = "\n".join([
            json.dumps({"id": 1, "user": "alice", "body": "fix this",
                        "path": "a.py", "line": 10, "user_type": "User",
                        "in_reply_to_id": None}),
            json.dumps({"id": 2, "user": "koan-bot", "body": "done",
                        "path": "a.py", "line": 10, "user_type": "User",
                        "in_reply_to_id": 1}),
        ])
        result = _fetch_inline_review_comments(
            "owner/repo", "1", bot_username="koan-bot", max_thread_depth=0,
        )
        assert result == []

    @patch("app.review_runner.run_gh")
    def test_max_depth_excludes_deep_thread(self, mock_gh):
        from app.review_runner import _fetch_inline_review_comments

        comments = [
            {"id": 1, "user": "alice", "body": "q1", "path": "a.py",
             "line": 1, "user_type": "User", "in_reply_to_id": None},
        ]
        for i in range(2, 7):
            comments.append(
                {"id": i, "user": "alice" if i % 2 == 0 else "koan-bot",
                 "body": f"reply {i}", "path": "a.py", "line": 1,
                 "user_type": "User", "in_reply_to_id": 1}
            )
        mock_gh.return_value = "\n".join(json.dumps(c) for c in comments)

        result = _fetch_inline_review_comments(
            "owner/repo", "1", bot_username="koan-bot", max_thread_depth=5,
        )
        assert result == []


class TestReviewReplyConfig:
    """Tests for get_review_reply_config in app.config."""

    def test_defaults(self):
        from app.config import get_review_reply_config
        with patch("app.config._load_config", return_value={}):
            cfg = get_review_reply_config()
            assert cfg == {"max_thread_depth": 5}

    def test_custom_value(self):
        from app.config import get_review_reply_config
        with patch("app.config._load_config",
                   return_value={"review_reply": {"max_thread_depth": 10}}):
            cfg = get_review_reply_config()
            assert cfg == {"max_thread_depth": 10}

    def test_invalid_value_falls_back_to_default(self):
        from app.config import get_review_reply_config
        with patch("app.config._load_config",
                   return_value={"review_reply": {"max_thread_depth": "abc"}}):
            cfg = get_review_reply_config()
            assert cfg == {"max_thread_depth": 5}


# ---------------------------------------------------------------------------
# Concurrency config: get_review_concurrency_config
# ---------------------------------------------------------------------------

class TestReviewConcurrencyConfig:
    """Tests for get_review_concurrency_config() in app.config."""

    def test_defaults_when_no_config(self):
        """Returns sensible defaults when review_concurrency is absent from config."""
        from app.config import get_review_concurrency_config

        with patch("app.config._load_config", return_value={}):
            cfg = get_review_concurrency_config()

        assert cfg["enabled"] is True
        assert cfg["github_workers"] == 4

    def test_reads_enabled_flag(self):
        """Reads enabled flag from config."""
        from app.config import get_review_concurrency_config

        with patch("app.config._load_config", return_value={
            "review_concurrency": {"enabled": False, "github_workers": 2},
        }):
            cfg = get_review_concurrency_config()

        assert cfg["enabled"] is False
        assert cfg["github_workers"] == 2

    def test_invalid_workers_falls_back_to_default(self):
        """Non-integer github_workers falls back to 4."""
        from app.config import get_review_concurrency_config

        with patch("app.config._load_config", return_value={
            "review_concurrency": {"github_workers": "not-a-number"},
        }):
            cfg = get_review_concurrency_config()

        assert cfg["github_workers"] == 4

    def test_non_dict_config_uses_defaults(self):
        """A non-dict review_concurrency value uses defaults."""
        from app.config import get_review_concurrency_config

        with patch("app.config._load_config", return_value={
            "review_concurrency": "invalid",
        }):
            cfg = get_review_concurrency_config()

        assert cfg["enabled"] is True
        assert cfg["github_workers"] == 4


# Phase 3: SUMMARY_TAG + idempotent upsert (_post_review_comment)
# ---------------------------------------------------------------------------

class TestPostReviewCommentIdempotent:
    """Phase 3 — SUMMARY_TAG is prepended; PATCH used when existing comment found."""

    @patch("app.review_runner.run_gh")
    def test_summary_tag_prepended_on_new_post(self, mock_gh):
        """SUMMARY_TAG is always prepended to a newly posted review comment."""
        from app.review_markers import SUMMARY_TAG
        _post_review_comment("owner", "repo", "42", "LGTM")
        body_arg = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "LGTM" in a][0]
        assert body_arg.startswith(SUMMARY_TAG)

    @patch("app.review_runner.run_gh")
    def test_patch_used_when_existing_comment_found(self, mock_gh):
        """When existing_comment is provided, PATCH endpoint is used not POST."""
        existing = {"id": 555, "body": "old body", "user": "koan-bot"}
        _post_review_comment("owner", "repo", "42", "New review", existing)
        call_args = mock_gh.call_args[0]
        # Should use PATCH via 'api' endpoint, not 'pr comment'
        assert "api" in call_args
        assert "PATCH" in call_args
        assert "issues/comments/555" in " ".join(str(a) for a in call_args)

    @patch("app.review_runner.run_gh")
    def test_post_used_when_no_existing_comment(self, mock_gh):
        """Without existing_comment, the standard 'pr comment' POST is used."""
        _post_review_comment("owner", "repo", "42", "Review", None)
        call_args = mock_gh.call_args[0]
        assert "pr" in call_args
        assert "comment" in call_args

    @patch("app.review_runner.run_gh")
    def test_preserves_commit_ids_from_existing_comment(self, mock_gh):
        """Existing COMMIT_IDS block is carried forward into the updated body."""
        from app.review_markers import SUMMARY_TAG, COMMIT_IDS_START, COMMIT_IDS_END
        sha_block = f"{COMMIT_IDS_START}abc123\ndef456{COMMIT_IDS_END}"
        existing = {"id": 99, "body": f"{SUMMARY_TAG}\nold review\n{sha_block}", "user": "koan-bot"}
        _post_review_comment("owner", "repo", "42", "New review text", existing)
        body_arg = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "New review" in a][0]
        assert "abc123" in body_arg
        assert "<!-- koan-commits" in body_arg

    @patch("app.review_runner.run_gh")
    def test_preserves_commit_ids_from_new_single_comment_format(self, mock_gh):
        """New single-comment commit block is carried forward."""
        from app.review_markers import SUMMARY_TAG, build_hidden_commit_block
        sha_block = build_hidden_commit_block(["abc123", "def456"])
        existing = {"id": 99, "body": f"{SUMMARY_TAG}\nold review\n{sha_block}", "user": "koan-bot"}
        _post_review_comment("owner", "repo", "42", "New review text", existing)
        body_arg = [a for a in mock_gh.call_args[0] if isinstance(a, str) and "New review" in a][0]
        assert "abc123" in body_arg
        assert "def456" in body_arg

    @patch("app.review_runner.run_gh")
    def test_patch_403_falls_back_to_new_comment(self, mock_gh):
        """If PATCH fails (e.g. the existing comment belongs to another bot
        account), fall back to posting a fresh comment instead of failing.

        Defends the bot-switch case end-to-end: even if a foreign comment
        slips through the author filter, the review still lands rather than
        surfacing a 403 permissions error.
        """
        def _side_effect(*args, **kwargs):
            if "PATCH" in args:
                raise RuntimeError("HTTP 403: Resource not accessible by integration")
            return ""

        mock_gh.side_effect = _side_effect
        existing = {"id": 555, "body": "old body", "user": "other-bot"}
        success, error = _post_review_comment("owner", "repo", "42", "New review", existing)
        assert success is True
        # Last call should be the POST fallback ('pr comment'), not PATCH
        last_call = mock_gh.call_args[0]
        assert "pr" in last_call
        assert "comment" in last_call



# ---------------------------------------------------------------------------
# Phase 5: Reviewed-commit SHAs (_fetch_pr_commit_shas, run_review)
# ---------------------------------------------------------------------------

class TestFetchPrCommitShas:
    @patch("app.review_runner.run_gh", return_value="abc123\ndef456\n")
    def test_returns_list_of_shas(self, mock_gh):
        result = _fetch_pr_commit_shas("owner", "repo", "42")
        assert result == ["abc123", "def456"]

    @patch("app.review_runner.run_gh", return_value="")
    def test_returns_empty_list_on_no_output(self, mock_gh):
        assert _fetch_pr_commit_shas("owner", "repo", "42") == []

    @patch("app.review_runner.run_gh", side_effect=RuntimeError("API error"))
    def test_returns_empty_list_on_error(self, mock_gh):
        assert _fetch_pr_commit_shas("owner", "repo", "42") == []

    @patch("app.review_runner.run_gh", return_value="abc123\ndef456\n")
    def test_calls_correct_endpoint(self, mock_gh):
        _fetch_pr_commit_shas("owner", "repo", "42")
        call_args = mock_gh.call_args[0]
        assert "repos/owner/repo/pulls/42/commits" in " ".join(str(a) for a in call_args)
        assert "--paginate" in call_args


class TestIncrementalReview:
    """Phase 5 — commit SHAs embedded in comment; second run skips known commits."""

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=["abc", "def"])

    @patch("app.review_runner.find_bot_comment")
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_skips_review_when_all_shas_already_reviewed(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas, pr_context, review_skill_dir,
    ):
        """When prior SHA block matches current commits exactly, review is skipped."""
        from app.review_markers import SUMMARY_TAG, COMMIT_IDS_START, COMMIT_IDS_END

        mock_fetch.return_value = pr_context
        sha_block = f"{COMMIT_IDS_START}\nabc\ndef\n{COMMIT_IDS_END}"
        prior_comment = {
            "id": 42,
            "body": f"{SUMMARY_TAG}\n## Review\n\n{sha_block}",
            "user": "koan-bot",
        }
        mock_find_bot.return_value = prior_comment

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert "no new commits" in summary.lower()
        # Claude should NOT have been called — review was skipped
        mock_claude.assert_not_called()

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=["abc", "def", "ghi"])

    @patch("app.review_runner.find_bot_comment")
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_proceeds_when_new_commits_present(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas, pr_context, review_skill_dir,
    ):
        """When there are new commits beyond prior SHAs, review proceeds."""
        from app.review_markers import SUMMARY_TAG, COMMIT_IDS_START, COMMIT_IDS_END

        mock_fetch.return_value = pr_context
        # Prior comment only has 2 SHAs; current has 3 → one new commit
        sha_block = f"{COMMIT_IDS_START}\nabc\ndef\n{COMMIT_IDS_END}"
        prior_comment = {"id": 42, "body": f"{SUMMARY_TAG}\n{sha_block}", "user": "koan-bot"}
        mock_find_bot.return_value = prior_comment

        # After posting the review, find_bot_comment is called again to get updated comment
        updated_comment = {"id": 42, "body": f"{SUMMARY_TAG}\n## Review\n\nLGTM", "user": "koan-bot"}
        mock_find_bot.side_effect = [prior_comment, updated_comment]

        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        mock_claude.assert_called_once()

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=["abc", "def"])

    @patch("app.review_runner.find_bot_comment")
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_sha_block_written_to_comment_after_review(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas, pr_context, review_skill_dir,
    ):
        """Commit SHAs are embedded in the initial comment body (no extra PATCH)."""
        mock_fetch.return_value = pr_context
        mock_find_bot.return_value = None  # No prior comment
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        comment_calls = [
            c for c in mock_gh.call_args_list
            if "comment" in c[0]
        ]
        assert len(comment_calls) >= 1
        body_arg = " ".join(str(a) for a in comment_calls[0][0])
        assert "<!-- koan-commits" in body_arg
        assert "abc" in body_arg
        assert "def" in body_arg
        assert "HEAD=def" in body_arg
        patch_calls = [
            c for c in mock_gh.call_args_list
            if len(c[0]) > 1 and "PATCH" in c[0]
        ]
        assert len(patch_calls) == 0


# ---------------------------------------------------------------------------
# Re-review: fresh comment on new commits
# ---------------------------------------------------------------------------


class TestReReviewFreshComment:
    """When new commits land after a prior review, post a fresh comment
    instead of PATCHing the old one (GitHub doesn't notify on edits)."""

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=["abc", "def", "ghi"])
    @patch("app.review_runner.find_bot_comment")
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_re_review_posts_new_comment_not_patch(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas, pr_context, review_skill_dir,
    ):
        """Re-review with new commits posts a fresh comment (not PATCH)."""
        from app.review_markers import SUMMARY_TAG, COMMIT_IDS_START, COMMIT_IDS_END

        mock_fetch.return_value = pr_context
        # Prior review had 2 SHAs; current has 3 → new commits
        sha_block = f"{COMMIT_IDS_START}\nabc\ndef\n{COMMIT_IDS_END}"
        prior_comment = {"id": 99, "body": f"{SUMMARY_TAG}\n{sha_block}", "user": "koan-bot"}
        mock_find_bot.return_value = prior_comment
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        # Should have a "pr comment" call (POST), not a PATCH-only flow
        post_calls = [
            c for c in mock_gh.call_args_list
            if len(c[0]) >= 2 and c[0][0] == "pr" and c[0][1] == "comment"
        ]
        assert len(post_calls) >= 1, "Expected a fresh 'pr comment' call for re-review"

        # The old comment should be collapsed via a PATCH call
        collapse_calls = [
            c for c in mock_gh.call_args_list
            if any("PATCH" in str(a) for a in c[0])
            and any("99" in str(a) for a in c[0])
        ]
        assert len(collapse_calls) >= 1, "Expected old comment to be collapsed via PATCH"

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=["abc", "def"])
    @patch("app.review_runner.find_bot_comment")
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_same_shas_patches_existing_comment(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas, pr_context, review_skill_dir,
    ):
        """Re-review of same commits (retry) PATCHes the existing comment."""
        from app.review_markers import SUMMARY_TAG, COMMIT_IDS_START, COMMIT_IDS_END

        mock_fetch.return_value = pr_context
        # Prior and current SHAs are identical — should SKIP entirely
        sha_block = f"{COMMIT_IDS_START}\nabc\ndef\n{COMMIT_IDS_END}"
        prior_comment = {"id": 99, "body": f"{SUMMARY_TAG}\n{sha_block}", "user": "koan-bot"}
        mock_find_bot.return_value = prior_comment

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert "no new commits" in summary.lower()
        mock_claude.assert_not_called()


class TestCollapseOldReview:
    """Tests for _collapse_old_review helper."""

    @patch("app.review_runner.run_gh")
    def test_collapses_comment(self, mock_gh):
        """Patches the old comment body to a superseded notice."""
        comment = {"id": 42, "body": "## Full Review\n\nLots of findings..."}
        _collapse_old_review("owner", "repo", comment)

        mock_gh.assert_called_once()
        args = mock_gh.call_args[0]
        assert "PATCH" in args
        assert "42" in str(args)

    @patch("app.review_runner.run_gh")
    def test_collapse_failure_does_not_raise(self, mock_gh):
        """Collapse failure is logged but doesn't block the new review."""
        mock_gh.side_effect = RuntimeError("API error")
        comment = {"id": 42, "body": "## Review"}
        _collapse_old_review("owner", "repo", comment)

    @patch("app.review_runner.run_gh")
    def test_collapse_skips_missing_id(self, mock_gh):
        """No API call when comment has no id."""
        _collapse_old_review("owner", "repo", {})
        mock_gh.assert_not_called()


# ---------------------------------------------------------------------------
# Review ignore config: get_review_ignore_config
# ---------------------------------------------------------------------------

class TestReviewIgnoreConfig:
    """Tests for get_review_ignore_config() in app.config."""

    def test_defaults_when_no_config(self):
        """Returns empty lists when review_ignore is absent from config."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={}):
            cfg = get_review_ignore_config()

        assert cfg == {"glob": [], "regex": []}

    def test_reads_glob_and_regex(self):
        """Reads glob and regex lists from config."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={
            "review_ignore": {
                "glob": ["vendor/**", "*.lock"],
                "regex": [r".*\.pb\.go$"],
            },
        }):
            cfg = get_review_ignore_config()

        assert cfg["glob"] == ["vendor/**", "*.lock"]
        assert cfg["regex"] == [r".*\.pb\.go$"]

    def test_partial_config_glob_only(self):
        """Only glob patterns configured, regex defaults to []."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={
            "review_ignore": {"glob": ["*.min.js"]},
        }):
            cfg = get_review_ignore_config()

        assert cfg["glob"] == ["*.min.js"]
        assert cfg["regex"] == []

    def test_partial_config_regex_only(self):
        """Only regex patterns configured, glob defaults to []."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={
            "review_ignore": {"regex": [r"^docs/"]},
        }):
            cfg = get_review_ignore_config()

        assert cfg["glob"] == []
        assert cfg["regex"] == [r"^docs/"]

    def test_non_dict_config_returns_empty(self):
        """A non-dict review_ignore value returns empty lists."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={
            "review_ignore": "invalid",
        }):
            cfg = get_review_ignore_config()

        assert cfg == {"glob": [], "regex": []}

    def test_non_list_glob_returns_empty(self):
        """A non-list glob value returns empty list."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={
            "review_ignore": {"glob": "not-a-list"},
        }):
            cfg = get_review_ignore_config()

        assert cfg["glob"] == []

    def test_coerces_values_to_strings(self):
        """Non-string patterns are coerced to strings."""
        from app.config import get_review_ignore_config

        with patch("app.config._load_config", return_value={
            "review_ignore": {"glob": [123, True]},
        }):
            cfg = get_review_ignore_config()

        assert cfg["glob"] == ["123", "True"]


class TestReviewTriageConfig:
    """Tests for get_review_triage_config() in app.config."""

    def test_defaults_when_no_config(self):
        from app.config import get_review_triage_config

        with patch("app.config._load_config", return_value={}):
            cfg = get_review_triage_config()

        assert cfg == {
            "enabled": False,
            "skip_lockfiles": True,
            "skip_generated": True,
            "skip_whitespace_only": True,
            "skip_renames": True,
        }

    def test_enabled_with_defaults(self):
        from app.config import get_review_triage_config

        with patch("app.config._load_config", return_value={
            "review_triage": {"enabled": True},
        }):
            cfg = get_review_triage_config()

        assert cfg["enabled"] is True
        assert cfg["skip_lockfiles"] is True

    def test_selective_disable(self):
        from app.config import get_review_triage_config

        with patch("app.config._load_config", return_value={
            "review_triage": {
                "enabled": True,
                "skip_lockfiles": False,
                "skip_whitespace_only": False,
            },
        }):
            cfg = get_review_triage_config()

        assert cfg["enabled"] is True
        assert cfg["skip_lockfiles"] is False
        assert cfg["skip_whitespace_only"] is False
        assert cfg["skip_generated"] is True

    def test_non_dict_config_returns_defaults(self):
        from app.config import get_review_triage_config

        with patch("app.config._load_config", return_value={
            "review_triage": "invalid",
        }):
            cfg = get_review_triage_config()

        assert cfg["enabled"] is False

    def test_non_bool_values_use_defaults(self):
        from app.config import get_review_triage_config

        with patch("app.config._load_config", return_value={
            "review_triage": {"enabled": "yes", "skip_lockfiles": 42},
        }):
            cfg = get_review_triage_config()

        assert cfg["enabled"] is False
        assert cfg["skip_lockfiles"] is True


# ---------------------------------------------------------------------------
# run_review with review_ignore filtering (from config.yaml)
# ---------------------------------------------------------------------------

_MULTI_FILE_DIFF = (
    "diff --git a/src/app.py b/src/app.py\n"
    "index abc..def 100644\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1 +1,2 @@\n"
    " x = 1\n"
    "+y = 2\n"
    "diff --git a/vendor/lodash.js b/vendor/lodash.js\n"
    "index 111..222 100644\n"
    "--- a/vendor/lodash.js\n"
    "+++ b/vendor/lodash.js\n"
    "@@ -1 +1,2 @@\n"
    " // vendored\n"
    "+// updated\n"
    "diff --git a/package-lock.json b/package-lock.json\n"
    "index 333..444 100644\n"
    "--- a/package-lock.json\n"
    "+++ b/package-lock.json\n"
    "@@ -1 +1 @@\n"
    "-{}\n"
    '+{"v": 2}\n'
)


class TestRunReviewWithIgnoreFilter:
    """Tests that review_ignore from config.yaml is applied in run_review()."""

    def _make_pr_context(self, diff=None):
        return {
            "title": "Test PR",
            "body": "",
            "branch": "feature",
            "base": "main",
            "state": "OPEN",
            "author": "dev",
            "url": "https://github.com/owner/repo/pull/1",
            "diff": diff or _MULTI_FILE_DIFF,
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.config.get_review_ignore_config", return_value={"glob": ["vendor/**", "*.json"], "regex": []})
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_review_ignore_glob_filters_diff_before_prompt(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, mock_ignore,
        mock_find_bot, _mock_shas, review_skill_dir,
    ):
        """Files matching review_ignore.glob are stripped from the diff before Claude."""
        mock_fetch.return_value = self._make_pr_context()
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        # The prompt passed to Claude should not contain vendor or lock files
        call_args = mock_claude.call_args
        prompt_sent = call_args[0][0]  # first positional arg
        assert "vendor/lodash.js" not in prompt_sent
        assert "package-lock.json" not in prompt_sent
        assert "src/app.py" in prompt_sent

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.config.get_review_ignore_config", return_value={"glob": ["**"], "regex": []})
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_all_files_ignored_returns_nothing_to_review(
        self, mock_fetch, mock_claude, mock_repliable, mock_ignore,
        mock_find_bot, _mock_shas, review_skill_dir,
    ):
        """When all files are ignored the review exits as a clean no-op."""
        mock_fetch.return_value = self._make_pr_context()

        success, summary, _ = run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        assert "nothing to review" in summary.lower()
        mock_claude.assert_not_called()

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.config.get_review_ignore_config", return_value={"glob": [], "regex": []})
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_no_ignore_config_no_filtering(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, mock_ignore,
        mock_find_bot, _mock_shas, review_skill_dir,
    ):
        """Without review_ignore patterns, the full diff reaches Claude unchanged."""
        mock_fetch.return_value = self._make_pr_context()
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        prompt_sent = mock_claude.call_args[0][0]
        assert "vendor/lodash.js" in prompt_sent
        assert "package-lock.json" in prompt_sent

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])

    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.config.get_review_ignore_config", return_value={"glob": [], "regex": []})
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_empty_ignore_patterns_no_filtering(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable, mock_ignore,
        mock_find_bot, _mock_shas, review_skill_dir,
    ):
        """Empty review_ignore lists leaves diff unchanged."""
        mock_fetch.return_value = self._make_pr_context()
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        prompt_sent = mock_claude.call_args[0][0]
        assert "vendor/lodash.js" in prompt_sent


# ---------------------------------------------------------------------------
# run_review with triage filtering (content-aware)
# ---------------------------------------------------------------------------


class TestRunReviewWithTriageFilter:
    """Tests that review_triage classification is applied in run_review()."""

    _TRIAGE_DIFF = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index abc..def 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        "+y = 2\n"
        "diff --git a/package-lock.json b/package-lock.json\n"
        "index 111..222 100644\n"
        "--- a/package-lock.json\n"
        "+++ b/package-lock.json\n"
        "@@ -1 +1,2 @@\n"
        ' "v": "1"\n'
        '+  "v": "2"\n'
    )

    def _make_pr_context(self, diff=None):
        return {
            "title": "Test PR",
            "body": "",
            "branch": "feature",
            "base": "main",
            "state": "OPEN",
            "author": "dev",
            "url": "https://github.com/owner/repo/pull/1",
            "diff": diff or self._TRIAGE_DIFF,
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
        }

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.config.get_review_ignore_config", return_value={"glob": [], "regex": []})
    @patch("app.config.get_review_triage_config", return_value={
        "enabled": True,
        "skip_lockfiles": True,
        "skip_generated": True,
        "skip_whitespace_only": True,
        "skip_renames": True,
    })
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_triage_filters_lockfile_from_prompt(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_triage_cfg, mock_ignore, mock_find_bot, _mock_shas,
        review_skill_dir,
    ):
        """Lock files are triaged out of the diff before Claude prompt."""
        mock_fetch.return_value = self._make_pr_context()
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        prompt_sent = mock_claude.call_args[0][0]
        assert "diff --git a/package-lock.json" not in prompt_sent
        assert "Triaged 1 trivial" in prompt_sent
        assert "src/app.py" in prompt_sent

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.config.get_review_ignore_config", return_value={"glob": [], "regex": []})
    @patch("app.config.get_review_triage_config", return_value={
        "enabled": False,
        "skip_lockfiles": True,
        "skip_generated": True,
        "skip_whitespace_only": True,
        "skip_renames": True,
    })
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_triage_disabled_keeps_all_files(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_triage_cfg, mock_ignore, mock_find_bot, _mock_shas,
        review_skill_dir,
    ):
        """When triage is disabled, lock files remain in the diff."""
        mock_fetch.return_value = self._make_pr_context()
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        prompt_sent = mock_claude.call_args[0][0]
        assert "package-lock.json" in prompt_sent


# Severity filter hint in review output
# ---------------------------------------------------------------------------

class TestSeverityFilterHint:
    def test_hint_shown_with_multiple_severities(self):
        data = {
            "file_comments": [
                {"file": "a.py", "line_start": 1, "line_end": 1,
                 "severity": "critical", "title": "Bug", "comment": "Fix",
                 "code_snippet": ""},
                {"file": "b.py", "line_start": 2, "line_end": 2,
                 "severity": "suggestion", "title": "Style", "comment": "Rename",
                 "code_snippet": ""},
            ],
            "review_summary": {"lgtm": False, "summary": "Needs work.",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "/rebase" in md
        assert "critical" in md.split("/rebase")[1]

    def test_hint_hidden_with_single_severity(self):
        data = {
            "file_comments": [
                {"file": "a.py", "line_start": 1, "line_end": 1,
                 "severity": "critical", "title": "Bug", "comment": "Fix",
                 "code_snippet": ""},
            ],
            "review_summary": {"lgtm": False, "summary": "Needs work.",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "/rebase" not in md

    def test_hint_hidden_on_lgtm(self):
        data = {
            "file_comments": [],
            "review_summary": {"lgtm": True, "summary": "LGTM",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "/rebase" not in md


# ---------------------------------------------------------------------------
# _safe_code_fence / _fix_nested_fences
# ---------------------------------------------------------------------------

class TestSafeCodeFence:
    def test_no_backticks_returns_triple(self):
        assert _safe_code_fence("print('hello')") == "```"

    def test_triple_backtick_in_content(self):
        assert _safe_code_fence("some ```bash\ncode\n```") == "````"

    def test_quad_backtick_in_content(self):
        assert _safe_code_fence("````python\ncode\n````") == "`````"


class TestFixNestedFences:
    def test_no_fences_unchanged(self):
        text = "Plain text\nwith no fences"
        assert _fix_nested_fences(text) == text

    def test_simple_fence_unchanged(self):
        text = "```python\nprint('hi')\n```"
        assert _fix_nested_fences(text) == text

    def test_nested_backticks_get_upgraded(self):
        text = "```bash\necho '```'\n```"
        result = _fix_nested_fences(text)
        assert result.startswith("````bash\n")
        assert result.endswith("\n````")
        assert "echo '```'" in result

    def test_deeply_nested_backticks(self):
        text = "```\ncode with ````inside\n```"
        result = _fix_nested_fences(text)
        assert result.startswith("`````\n")
        assert result.endswith("\n`````")

    def test_multiple_fences_independently_fixed(self):
        text = "text\n```\nclean\n```\nmore\n```\nhas ``` inside\n```"
        result = _fix_nested_fences(text)
        lines = result.split("\n")
        assert lines[0] == "text"
        assert lines[1] == "```"
        assert lines[3] == "```"
        assert lines[5] == "````"
        assert lines[7] == "````"


class TestNestedFencesInReview:
    def test_code_snippet_with_backticks(self):
        data = {
            "file_comments": [
                {"file": "readme.md", "line_start": 5, "line_end": 5,
                 "severity": "warning", "title": "Bad fence",
                 "comment": "Fix this",
                 "code_snippet": "```bash\necho hello\n```"},
            ],
            "review_summary": {"lgtm": False, "summary": "Fix.",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "````" in md
        assert md.count("````") % 2 == 0

    def test_comment_with_nested_fences(self):
        data = {
            "file_comments": [
                {"file": "app.py", "line_start": 1, "line_end": 1,
                 "severity": "critical", "title": "Issue",
                 "comment": "Use this pattern:\n\n```python\nx = '```'\n```",
                 "code_snippet": ""},
            ],
            "review_summary": {"lgtm": False, "summary": "Fix.",
                               "checklist": []},
        }
        md = _format_review_as_markdown(data)
        assert "````python" in md


# ---------------------------------------------------------------------------
# _reflect_findings
# ---------------------------------------------------------------------------

class TestReflectFindings:
    """Tests for the second-pass reflection filter."""

    FINDING = {
        "file": "auth.py",
        "line_start": 10,
        "line_end": 10,
        "severity": "warning",
        "title": "Missing check",
        "comment": "Add validation.",
        "code_snippet": "",
    }

    @pytest.fixture
    def skill_dir(self, tmp_path):
        """Create a minimal skill dir with a reflect prompt."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "reflect.md").write_text(
            "Reflect: {FINDINGS_JSON}\nDiff: {DIFF}"
        )
        return tmp_path

    def _make_scores(self, *scores):
        """Build a reflect JSON response with given scores for findings 0..N."""
        return json.dumps([
            {"finding_index": i, "score": s, "reason": f"reason {i}"}
            for i, s in enumerate(scores)
        ])

    @patch("app.review_runner._run_claude_review")
    def test_happy_path_filters_below_threshold(self, mock_claude, skill_dir):
        """Findings with score < threshold are dropped."""
        from app.review_runner import _reflect_findings

        findings = [dict(self.FINDING), dict(self.FINDING), dict(self.FINDING)]
        mock_claude.return_value = (self._make_scores(3, 7, 5), "")

        result = _reflect_findings(findings, "diff text", "/tmp/p", "haiku", 5, skill_dir=skill_dir)

        assert len(result) == 2
        assert result[0] is findings[1]
        assert result[1] is findings[2]

    @patch("app.review_runner._run_claude_review")
    def test_parse_failure_returns_original(self, mock_claude, skill_dir):
        """When Claude returns invalid JSON, original findings are returned unchanged."""
        from app.review_runner import _reflect_findings

        findings = [dict(self.FINDING)]
        mock_claude.return_value = ("not json at all", "")

        result = _reflect_findings(findings, "diff", "/tmp/p", None, 5, skill_dir=skill_dir)

        assert result is findings

    @patch("app.review_runner._run_claude_review")
    def test_threshold_zero_short_circuits(self, mock_claude, skill_dir):
        """threshold=0 returns all findings without calling Claude."""
        from app.review_runner import _reflect_findings

        findings = [dict(self.FINDING), dict(self.FINDING)]

        result = _reflect_findings(findings, "diff", "/tmp/p", None, 0, skill_dir=skill_dir)

        assert len(result) == 2
        mock_claude.assert_not_called()

    @patch("app.review_runner._run_claude_review")
    def test_threshold_ten_all_filtered(self, mock_claude, skill_dir):
        """threshold=10 drops all findings unless they score exactly 10."""
        from app.review_runner import _reflect_findings

        findings = [dict(self.FINDING), dict(self.FINDING)]
        mock_claude.return_value = (self._make_scores(8, 9), "")

        result = _reflect_findings(findings, "diff", "/tmp/p", None, 10, skill_dir=skill_dir)

        assert result == []

    @patch("app.review_runner._run_claude_review")
    def test_empty_findings_skips_claude(self, mock_claude, skill_dir):
        """Empty findings list returns immediately without calling Claude."""
        from app.review_runner import _reflect_findings

        result = _reflect_findings([], "diff", "/tmp/p", None, 5, skill_dir=skill_dir)

        assert result == []
        mock_claude.assert_not_called()

    @patch("app.review_runner._run_claude_review")
    def test_claude_error_returns_original(self, mock_claude, skill_dir):
        """When Claude call fails, original findings are returned unchanged."""
        from app.review_runner import _reflect_findings

        findings = [dict(self.FINDING)]
        mock_claude.return_value = ("", "timeout")

        result = _reflect_findings(findings, "diff", "/tmp/p", None, 5, skill_dir=skill_dir)

        assert result is findings

    @patch("app.review_runner._run_claude_review")
    def test_out_of_range_indices_ignored(self, mock_claude, skill_dir):
        """Reflection entries with out-of-range finding_index are silently skipped."""
        from app.review_runner import _reflect_findings

        findings = [dict(self.FINDING)]
        scores = json.dumps([
            {"finding_index": 0, "score": 8, "reason": "ok"},
            {"finding_index": 99, "score": 0, "reason": "phantom"},
        ])
        mock_claude.return_value = (scores, "")

        result = _reflect_findings(findings, "diff", "/tmp/p", None, 5, skill_dir=skill_dir)

        assert len(result) == 1


# ---------------------------------------------------------------------------
# _run_claude_review with model override
# ---------------------------------------------------------------------------

class TestRunClaudeReviewModelOverride:
    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config")
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_model_override_passed_to_streaming_runner(
        self, mock_max_turns, mock_models, mock_run,
    ):
        """model override is passed through instead of using models['mission']."""
        from app.review_runner import _run_claude_review

        mock_run.return_value = "ok"
        _run_claude_review("prompt", "/tmp/project", model="haiku")

        _, kwargs = mock_run.call_args
        assert kwargs.get("model") == "haiku"
        mock_models.assert_not_called()

    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config", return_value={"review_mode": "review-model", "mission": "mission-model"})
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_none_model_uses_review_mode_default(
        self, mock_max_turns, mock_models, mock_run,
    ):
        """When model=None, /review uses models['review_mode']."""
        from app.review_runner import _run_claude_review

        mock_run.return_value = "ok"
        _run_claude_review("prompt", "/tmp/project", model=None)

        _, kwargs = mock_run.call_args
        assert kwargs.get("model") == "review-model"
        assert kwargs.get("model_key") == "mission"

    @patch("app.cli_provider.run_command_streaming")
    @patch("app.config.get_model_config", return_value={"review_mode": "", "mission": "mission-model"})
    @patch("app.config.get_skill_max_turns", return_value=200)
    def test_none_model_falls_back_to_mission_default(
        self, mock_max_turns, mock_models, mock_run,
    ):
        """When review_mode is empty, /review falls back to models['mission']."""
        from app.review_runner import _run_claude_review

        mock_run.return_value = "ok"
        _run_claude_review("prompt", "/tmp/project", model=None)

        _, kwargs = mock_run.call_args
        assert kwargs.get("model") == "mission-model"
        assert kwargs.get("model_key") == "mission"


# ---------------------------------------------------------------------------
# run_review reflection integration
# ---------------------------------------------------------------------------

class TestRunReviewReflectionIntegration:
    """Verify run_review calls _reflect_findings and uses its output."""

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._reflect_findings")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    @patch("app.config.get_review_reflect_config", return_value={"threshold": 5})
    @patch("app.config.get_model_config", return_value={
        "mission": "m", "fallback": "f", "reflect": "haiku", "lightweight": "haiku",
    })
    @patch("app.config.get_review_ignore_config", return_value={"glob": [], "regex": []})
    def test_reflect_called_with_file_comments(
        self, _mock_ignore, _mock_models, _mock_reflect_cfg,
        mock_fetch, mock_claude, mock_reflect, mock_gh, _mock_repliable, _mock_shas,
        review_skill_dir,
    ):
        """_reflect_findings is called when file_comments is non-empty."""
        pr_ctx = {
            "title": "t", "body": "", "branch": "b", "base": "main",
            "state": "OPEN", "author": "a", "url": "u",
            "diff": "some diff",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_fetch.return_value = pr_ctx
        mock_claude.return_value = (json.dumps(VALID_REVIEW_JSON), "")
        mock_reflect.return_value = []

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        mock_reflect.assert_called_once()
        call_args = mock_reflect.call_args
        assert call_args[0][0] == VALID_REVIEW_JSON["file_comments"]
        assert call_args[0][1] == "some diff"

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._reflect_findings")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    @patch("app.config.get_review_reflect_config", return_value={"threshold": 5})
    @patch("app.config.get_model_config", return_value={
        "mission": "m", "fallback": "f", "reflect": "haiku", "lightweight": "haiku",
    })
    @patch("app.config.get_review_ignore_config", return_value={"glob": [], "regex": []})
    def test_reflect_not_called_when_no_file_comments(
        self, _mock_ignore, _mock_models, _mock_reflect_cfg,
        mock_fetch, mock_claude, mock_reflect, mock_gh, _mock_repliable, _mock_shas,
        review_skill_dir,
    ):
        """_reflect_findings is NOT called when file_comments is empty."""
        pr_ctx = {
            "title": "t", "body": "", "branch": "b", "base": "main",
            "state": "OPEN", "author": "a", "url": "u",
            "diff": "some diff",
            "review_comments": "", "reviews": "", "issue_comments": "",
        }
        mock_fetch.return_value = pr_ctx
        mock_claude.return_value = (json.dumps(LGTM_REVIEW_JSON), "")

        run_review(
            "owner", "repo", "1", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        mock_reflect.assert_not_called()


# ---------------------------------------------------------------------------
# close_pr field: review-driven PR closure
# ---------------------------------------------------------------------------

_CLOSE_REVIEW_JSON_TEMPLATE = {
    "file_comments": [],
    "review_summary": {
        "lgtm": False,
        "summary": "Maintainer requested closure.",
        "checklist": [],
    },
    "close_pr": {
        "close": True,
        "reason": "Maintainer asked to close — existing low-level API covers the use case.",
    },
}


def _make_close_review_json():
    """Return a fresh deep copy so tests can mutate nested dicts safely."""
    return copy.deepcopy(_CLOSE_REVIEW_JSON_TEMPLATE)


class TestRunReviewClosePr:
    """run_review() must execute gh pr close when close_pr.close is True.

    Regression: dbus-fast PR #639 — bot posted a comment saying "Closing this..."
    but never ran `gh pr close`, so the PR stayed open and a queued rebase fired
    on top of the supposedly-closed PR. The fix wires the structured close_pr
    field through to an actual `gh pr close` call.
    """

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_close_pr_true_runs_gh_pr_close(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas,
        pr_context, review_skill_dir,
    ):
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(_make_close_review_json()), "")
        mock_notify = MagicMock()

        success, summary, review_data = run_review(
            "owner", "repo", "639", "/tmp/project",
            notify_fn=mock_notify,
            skill_dir=review_skill_dir,
        )

        assert success is True
        # Must include an atomic `gh pr close --comment ...` call that
        # carries the explanatory comment, so close+comment can't desync.
        gh_call_args = [list(call.args) for call in mock_gh.call_args_list]
        close_calls = [
            args for args in gh_call_args
            if args[:2] == ["pr", "close"] and "639" in args
        ]
        assert close_calls, f"Expected `gh pr close 639` in {gh_call_args}"
        assert any(
            "--comment" in args for args in close_calls
        ), f"Expected `gh pr close` to carry --comment in {close_calls}"
        # No separate `pr comment` round-trip for the closure explanation.
        assert not any(
            args[:2] == ["pr", "comment"]
            and "Closed by Reviewer" in " ".join(str(a) for a in args)
            for args in gh_call_args
        )
        # Summary should mention closure
        assert "closed" in summary.lower()
        assert any(
            "closed" in str(call.args[0]).lower()
            for call in mock_notify.call_args_list
        )

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_close_pr_false_does_not_close(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas,
        pr_context, review_skill_dir,
    ):
        mock_fetch.return_value = pr_context
        review_no_close = _make_close_review_json()
        review_no_close["close_pr"] = {"close": False, "reason": ""}
        mock_claude.return_value = (json.dumps(review_no_close), "")

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        gh_call_args = [list(call.args) for call in mock_gh.call_args_list]
        assert not any(args[:2] == ["pr", "close"] for args in gh_call_args)
        assert "closed" not in summary.lower()

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_close_pr_field_missing_does_not_close(
        self, mock_fetch, mock_claude, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """Legacy reviews without close_pr field stay safely open."""
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(VALID_REVIEW_JSON), "")

        success, _, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is True
        gh_call_args = [list(call.args) for call in mock_gh.call_args_list]
        assert not any(args[:2] == ["pr", "close"] for args in gh_call_args)

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_close_failure_still_reports_review_success(
        self, mock_fetch, mock_claude, mock_repliable,
        mock_find_bot, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """If `gh pr close` fails, the review itself still counts as posted.

        Also guards against the original ordering bug: no "PR Closed by
        Reviewer" comment should ever land on a PR that failed to close.
        With the atomic `gh pr close --comment` path, the failed close
        rolls back the comment automatically.
        """
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(_make_close_review_json()), "")

        captured_calls = []

        def gh_side_effect(*args, **kwargs):
            captured_calls.append(list(args))
            if args[:2] == ("pr", "close"):
                raise RuntimeError("403 forbidden")
            return ""

        with patch("app.review_runner.run_gh", side_effect=gh_side_effect):
            success, summary, _ = run_review(
                "owner", "repo", "42", "/tmp/project",
                notify_fn=MagicMock(),
                skill_dir=review_skill_dir,
            )

        assert success is True
        assert "PR closed" not in summary
        # No standalone `pr comment` was used to announce the closure —
        # the atomic --comment flag means a failed close leaves no
        # misleading "PR Closed" comment behind.
        assert not any(
            args[:2] == ["pr", "comment"]
            and "Closed by Reviewer" in " ".join(str(a) for a in args)
            for args in captured_calls
        )

    @patch("app.review_runner._fetch_pr_commit_shas", return_value=[])
    @patch("app.review_runner.find_bot_comment", return_value=None)
    @patch("app.review_runner.fetch_repliable_comments", return_value=[])
    @patch("app.review_runner.run_gh")
    @patch("app.review_runner._post_review_comment", return_value=(False, "rate limited"))
    @patch("app.review_runner._run_claude_review")
    @patch("app.review_runner.fetch_pr_context")
    def test_close_pr_skipped_when_review_post_fails(
        self, mock_fetch, mock_claude, mock_post, mock_gh, mock_repliable,
        mock_find_bot, _mock_shas,
        pr_context, review_skill_dir,
    ):
        """When the review post fails, closure is suppressed even if close_pr.close=True.

        Locks in the conservative behavior: never close a PR if the review body
        explaining the closure never made it onto the PR.
        """
        mock_fetch.return_value = pr_context
        mock_claude.return_value = (json.dumps(_make_close_review_json()), "")

        success, summary, _ = run_review(
            "owner", "repo", "42", "/tmp/project",
            notify_fn=MagicMock(),
            skill_dir=review_skill_dir,
        )

        assert success is False
        gh_call_args = [list(call.args) for call in mock_gh.call_args_list]
        assert not any(args[:2] == ["pr", "close"] for args in gh_call_args)
        assert "closed" not in summary.lower()


# ---------------------------------------------------------------------------
# review_schema: close_pr field validation
# ---------------------------------------------------------------------------


class TestReviewSchemaClosePr:
    """validate_review() must accept and validate the optional close_pr field."""

    def _base(self):
        return {
            "file_comments": [],
            "review_summary": {
                "lgtm": True,
                "summary": "ok",
                "checklist": [],
            },
        }

    def test_close_pr_omitted_is_valid(self):
        from app.review_schema import validate_review

        ok, errors = validate_review(self._base())
        assert ok is True, errors

    def test_close_pr_valid_object_accepted(self):
        from app.review_schema import validate_review

        data = self._base()
        data["close_pr"] = {"close": True, "reason": "maintainer asked"}
        ok, errors = validate_review(data)
        assert ok is True, errors

    def test_close_pr_missing_close_field_rejected(self):
        from app.review_schema import validate_review

        data = self._base()
        data["close_pr"] = {"reason": "x"}
        ok, errors = validate_review(data)
        assert ok is False
        assert any("close" in e for e in errors)

    def test_close_pr_wrong_types_rejected(self):
        from app.review_schema import validate_review

        data = self._base()
        data["close_pr"] = {"close": "yes", "reason": 42}
        ok, errors = validate_review(data)
        assert ok is False
        assert any("close_pr.close" in e for e in errors)
        assert any("close_pr.reason" in e for e in errors)

    def test_close_pr_must_be_object(self):
        from app.review_schema import validate_review

        data = self._base()
        data["close_pr"] = True
        ok, errors = validate_review(data)
        assert ok is False
        assert any("close_pr" in e for e in errors)


# ---------------------------------------------------------------------------
# Silent Failure Analysis Formatting
# ---------------------------------------------------------------------------


class TestFormatErrorHunterFindings:
    """Tests for _format_error_hunter_findings collapsible output."""

    def test_basic_collapsible_structure(self):
        from app.review_runner import _format_error_hunter_findings

        findings = [
            {
                "severity": "HIGH",
                "pattern": "swallowed exception",
                "file": "src/auth.py",
                "line_hint": "42",
                "snippet": "except Exception:\n    pass",
                "explanation": "Exception silently ignored",
                "suggestion": "Log or re-raise the exception",
            }
        ]
        result = _format_error_hunter_findings(findings)
        assert "## Silent Failure Analysis" in result
        assert "<details>" in result
        assert "<summary>" in result
        assert "</details>" in result
        assert "HIGH" in result
        assert "swallowed exception" in result
        assert "`src/auth.py:42`" in result

    def test_severity_sorting(self):
        from app.review_runner import _format_error_hunter_findings

        findings = [
            {"severity": "MEDIUM", "pattern": "medium issue", "file": "a.py"},
            {"severity": "CRITICAL", "pattern": "critical issue", "file": "b.py"},
            {"severity": "HIGH", "pattern": "high issue", "file": "c.py"},
        ]
        result = _format_error_hunter_findings(findings)
        crit_pos = result.index("critical issue")
        high_pos = result.index("high issue")
        med_pos = result.index("medium issue")
        assert crit_pos < high_pos < med_pos

    def test_emoji_per_severity(self):
        from app.review_runner import _format_error_hunter_findings

        findings = [
            {"severity": "CRITICAL", "pattern": "x", "file": "a.py"},
            {"severity": "HIGH", "pattern": "y", "file": "b.py"},
            {"severity": "MEDIUM", "pattern": "z", "file": "c.py"},
        ]
        result = _format_error_hunter_findings(findings)
        assert "🔴" in result
        assert "🟠" in result
        assert "🟡" in result

    def test_details_wrapped_content(self):
        from app.review_runner import _format_error_hunter_findings

        findings = [
            {
                "severity": "HIGH",
                "pattern": "silent null",
                "file": "svc.py",
                "line_hint": "10",
                "snippet": "return None",
                "explanation": "Hides upstream failure",
                "suggestion": "Raise ValueError",
            }
        ]
        result = _format_error_hunter_findings(findings)
        # Explanation and snippet should be inside details (after summary, before </details>)
        details_start = result.index("<details>")
        details_end = result.index("</details>")
        inner = result[details_start:details_end]
        assert "Hides upstream failure" in inner
        assert "return None" in inner
        assert "Raise ValueError" in inner

    def test_missing_optional_fields(self):
        from app.review_runner import _format_error_hunter_findings

        findings = [{"severity": "MEDIUM", "pattern": "bare except"}]
        result = _format_error_hunter_findings(findings)
        assert "<details>" in result
        assert "bare except" in result
