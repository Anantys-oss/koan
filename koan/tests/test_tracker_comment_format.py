"""Tests for tracker_comment_format.py — Jira-specific branches and helpers."""

import os
import sys
import unittest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.jira_notifications import markdown_to_adf
from app.tracker_comment_format import (
    build_plan_comment_failure,
    build_plan_comment_success,
    build_pr_comment_failure,
    build_pr_comment_success,
    flatten_github_markdown_for_jira,
)


def _walk_adf(value):
    if isinstance(value, dict):
        yield value
        for child in value.get("content", []):
            yield from _walk_adf(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_adf(child)


class TestFlattenGitHubMarkdownForJira(unittest.TestCase):
    def test_preserves_standard_markdown_while_removing_details_html(self):
        result = flatten_github_markdown_for_jira(
            "## Step\n<details><summary>Test code</summary>\n```python\npass\n```\n</details>"
        )
        assert "## Step" in result
        assert "**Test code**" in result
        assert "```python" in result
        assert "<details>" not in result

    def test_details_inside_a_code_fence_is_left_verbatim(self):
        """`/plan` posts code examples; rewriting them corrupts the plan.

        Only the GitHub `details` wrapper around content should be flattened —
        the same text appearing *as* code must survive untouched.
        """
        result = flatten_github_markdown_for_jira(
            "Example:\n\n```html\n<details><summary>x</summary>body</details>\n```\n"
        )

        assert "<details><summary>x</summary>body</details>" in result
        assert "**x**" not in result


# ---------------------------------------------------------------------------
# build_pr_comment_success — Jira branch
# ---------------------------------------------------------------------------


class TestBuildPrCommentSuccessJira(unittest.TestCase):
    def _call(self, **kwargs):
        defaults = {
            "provider": "jira",
            "pr_url": "https://github.com/org/repo/pull/42",
            "pr_title": "fix: repair widget",
            "pr_body": "",
            "skill_name": "fix",
        }
        defaults.update(kwargs)
        return build_pr_comment_success(**defaults)

    def test_basic_header(self):
        result = self._call()
        assert result.startswith("### Kōan · draft pull request created")

    def test_mission_from_skill_name(self):
        result = self._call(skill_name="implement")
        assert "- **Mission**: `/implement`" in result

    def test_unknown_mission_when_empty_skill(self):
        result = self._call(skill_name="")
        assert "- **Mission**: `(unknown)`" in result

    def test_pr_url_included(self):
        result = self._call()
        assert "https://github.com/org/repo/pull/42" in result

    def test_pr_title_included(self):
        result = self._call(pr_title="fix: add index")
        assert "[PR #42 — fix: add index]" in result

    def test_pr_title_omitted_when_empty(self):
        result = self._call(pr_title="")
        assert "[PR #42](https://github.com/org/repo/pull/42)" in result

    def test_target_branch_included(self):
        result = self._call(base_branch="develop")
        assert "- **Target branch**: `develop`" in result

    def test_target_branch_omitted_when_none(self):
        result = self._call(base_branch=None)
        assert "Target branch:" not in result

    def test_what_section_from_summary(self):
        body = "## Summary\n- Added foo\n- Fixed bar"
        result = self._call(pr_body=body)
        assert "**What changed**" in result
        assert "- Added foo" in result
        assert "- Fixed bar" in result

    def test_what_section_from_changes(self):
        body = "## Changes\n- Rewrote module"
        result = self._call(pr_body=body)
        assert "**What changed**" in result
        assert "- Rewrote module" in result

    def test_why_section(self):
        body = "## Why\nPerformance regression."
        result = self._call(pr_body=body)
        assert "**Why**\nPerformance regression." in result

    def test_how_section(self):
        body = "## How\n- Used caching\n- Added index"
        result = self._call(pr_body=body)
        assert "**How it was implemented**" in result
        assert "- Used caching" in result

    def test_testing_section(self):
        body = "## Testing\n- Unit tests added\n- Manual QA"
        result = self._call(pr_body=body)
        assert "**Validation**" in result
        assert "- Unit tests added" in result

    def test_bullets_capped_at_eight(self):
        items = "\n".join(f"- item {i}" for i in range(12))
        body = f"## Summary\n{items}"
        result = self._call(pr_body=body)
        assert "- item 7" in result
        assert "- item 8" not in result

    def test_next_section_present(self):
        result = self._call()
        assert "**Next**" in result
        assert "Review the draft PR and merge when ready." in result

    def test_renders_structured_metadata_and_labelled_pr_link(self):
        result = self._call(
            pr_title="Repair widget validators",
            pr_body="## Summary\n- Added regression coverage",
            base_branch="140",
        )
        adf = markdown_to_adf(result)
        nodes = list(_walk_adf(adf))

        heading = adf["content"][0]
        assert heading["type"] == "heading"
        assert heading["attrs"]["level"] == 3
        assert heading["content"][0]["text"] == (
            "Kōan · draft pull request created"
        )
        assert any(node.get("type") == "bulletList" for node in nodes)

        link = next(
            node
            for node in nodes
            if any(mark["type"] == "link" for mark in node.get("marks", []))
        )
        assert link["text"] == "PR #42 — Repair widget validators"
        assert link["marks"][0]["attrs"]["href"] == (
            "https://github.com/org/repo/pull/42"
        )

        code_values = {
            node["text"]
            for node in nodes
            if any(mark["type"] == "code" for mark in node.get("marks", []))
        }
        assert {"/fix", "140"} <= code_values

    def test_pr_title_cannot_break_generated_link(self):
        result = self._call(
            pr_title="Fix [legacy] validation\nwithout coercion",
        )
        assert (
            "[PR #42 — Fix (legacy) validation without coercion]"
            "(https://github.com/org/repo/pull/42)"
        ) in result

    def test_github_branch_uses_markdown(self):
        """Contrast: GitHub branch should use markdown headings."""
        result = build_pr_comment_success(
            provider="github",
            pr_url="https://github.com/org/repo/pull/1",
            pr_title="fix",
            pr_body="",
        )
        assert "## Draft PR Created" in result


# ---------------------------------------------------------------------------
# build_pr_comment_failure — Jira branch
# ---------------------------------------------------------------------------


class TestBuildPrCommentFailureJira(unittest.TestCase):
    def _call(self, **kwargs):
        defaults = {
            "provider": "jira",
            "reason": "Permission denied",
            "branch": "koan/fix-widget",
            "skill_name": "fix",
        }
        defaults.update(kwargs)
        return build_pr_comment_failure(**defaults)

    def test_basic_header(self):
        result = self._call()
        assert result.startswith("### Kōan · pull request creation failed")

    def test_mission_name(self):
        result = self._call(skill_name="review")
        assert "- **Mission**: `/review`" in result

    def test_unknown_mission(self):
        result = self._call(skill_name="")
        assert "- **Mission**: `(unknown)`" in result

    def test_reason_included(self):
        result = self._call(reason="Rate limit exceeded")
        assert "- **Reason**: Rate limit exceeded" in result

    def test_reason_defaults_on_empty(self):
        result = self._call(reason="")
        assert "- **Reason**: Unknown error" in result

    def test_branch_included(self):
        result = self._call(branch="koan/fix-auth")
        assert "- **Current branch**: `koan/fix-auth`" in result

    def test_branch_omitted_when_empty(self):
        result = self._call(branch="")
        assert "Current branch:" not in result

    def test_target_branch_included(self):
        result = self._call(base_branch="main")
        assert "- **Target branch**: `main`" in result

    def test_target_branch_omitted_when_none(self):
        result = self._call(base_branch=None)
        assert "Target branch:" not in result

    def test_next_steps_present(self):
        result = self._call()
        assert "**Next**" in result
        assert "Check branch state and repository permissions." in result
        assert "Re-run the mission after fixing the blocking issue." in result

    def test_renders_structured_failure_adf(self):
        adf = markdown_to_adf(self._call(base_branch="main"))
        nodes = list(_walk_adf(adf))

        heading = adf["content"][0]
        assert heading["type"] == "heading"
        assert heading["attrs"] == {"level": 3}
        assert heading["content"][0]["text"] == (
            "Kōan · pull request creation failed"
        )
        assert any(node.get("type") == "bulletList" for node in nodes)
        code_values = {
            node["text"]
            for node in nodes
            if any(mark["type"] == "code" for mark in node.get("marks", []))
        }
        assert {"/fix", "koan/fix-widget", "main"} <= code_values
        assert any(
            any(mark["type"] == "strong" for mark in node.get("marks", []))
            for node in nodes
        )

    def test_github_branch_uses_markdown(self):
        result = build_pr_comment_failure(
            provider="github",
            reason="auth failed",
        )
        assert "## PR Creation Failed" in result


# ---------------------------------------------------------------------------
# build_plan_comment_success — Jira branch
# ---------------------------------------------------------------------------


class TestBuildPlanCommentSuccessJira(unittest.TestCase):
    def test_basic_structure(self):
        result = build_plan_comment_success(
            "jira", "Plan: Widget Revamp", "## Steps\n- Do A\n- Do B"
        )
        assert "## Plan: Widget Revamp" in result
        assert "Generated by Koan." in result

    def test_body_keeps_markdown_for_rich_adf_conversion(self):
        result = build_plan_comment_success(
            "jira", "Plan", "## Overview\n**Bold text** and `code`"
        )
        assert "## Plan" in result
        assert "## Overview" in result
        assert "**Bold text**" in result
        assert "`code`" in result
        assert "Bold text" in result
        assert "code" in result

    def test_github_uses_markdown(self):
        result = build_plan_comment_success(
            "github", "Plan Title", "## Steps\n- One"
        )
        assert "## Plan Title" in result
        assert "## Steps" in result


# ---------------------------------------------------------------------------
# build_plan_comment_failure — Jira branch
# ---------------------------------------------------------------------------


class TestBuildPlanCommentFailureJira(unittest.TestCase):
    def test_basic_structure(self):
        result = build_plan_comment_failure("jira", "Timeout reached")
        assert "Koan plan update failed." in result
        assert "Reason: Timeout reached" in result

    def test_empty_reason_defaults(self):
        result = build_plan_comment_failure("jira", "")
        assert "Reason: Unknown error" in result

    def test_next_steps_present(self):
        result = build_plan_comment_failure("jira", "error")
        assert "Next:" in result
        assert "Re-run /plan after resolving the issue above." in result

    def test_no_markdown_formatting(self):
        result = build_plan_comment_failure("jira", "some error")
        assert "##" not in result
        assert "`" not in result

    def test_github_uses_markdown(self):
        result = build_plan_comment_failure("github", "bad input")
        assert "## Plan Update Failed" in result


class TestGitHubAlertFlattening(unittest.TestCase):
    def test_warning_block_flattened(self):
        md = "> [!WARNING]\n> This is risky\n> Second line"
        result = flatten_github_markdown_for_jira(md)
        assert ">" not in result
        assert "[!" not in result
        assert "WARNING: This is risky" in result
        assert "Second line" in result

    def test_all_five_kinds(self):
        for kind in ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"):
            md = f"> [!{kind}]\n> body text"
            result = flatten_github_markdown_for_jira(md)
            assert f"{kind}: body text" in result
            assert "[!" not in result

    def test_alert_mixed_with_prose(self):
        md = "Intro line\n\n> [!NOTE]\n> Remember this\n\nOutro line"
        result = flatten_github_markdown_for_jira(md)
        assert "Intro line" in result
        assert "NOTE: Remember this" in result
        assert "Outro line" in result
        assert ">" not in result

    def test_plain_blockquote_untouched(self):
        # A human's ordinary Jira blockquote is NOT a GitHub alert opener.
        md = "> just a quoted sentence a human wrote"
        result = flatten_github_markdown_for_jira(md)
        assert "just a quoted sentence a human wrote" in result
        assert "NOTE:" not in result and "WARNING:" not in result

    def test_opener_with_trailing_text_not_treated_as_alert(self):
        # `> [!WARNING] inline` is not the exact opener form; leave it alone.
        md = "> [!WARNING] not really an alert opener"
        result = flatten_github_markdown_for_jira(md)
        assert "WARNING:" not in result

    def test_adjacent_blocks_without_blank_line(self):
        # A second opener terminates the first block's body run; both flatten
        # independently instead of the second folding in as literal `[!NOTE]`.
        md = "> [!WARNING]\n> a\n> [!NOTE]\n> b"
        result = flatten_github_markdown_for_jira(md)
        assert "[!" not in result
        assert "WARNING: a" in result
        assert "NOTE: b" in result

    def test_alert_syntax_inside_code_fence_preserved(self):
        # Alert syntax inside a fenced code block is example text, not an
        # alert to degrade — leave it verbatim.
        md = "```\n> [!WARNING]\n> literal example\n```"
        result = flatten_github_markdown_for_jira(md)
        assert "[!WARNING]" in result
        assert "WARNING: literal example" not in result


class TestPrCommentAlertSafety(unittest.TestCase):
    def test_success_why_section_alert_flattened(self):
        body = "## Why\n> [!WARNING]\n> Data loss possible\n\n## How\n- did it"
        result = build_pr_comment_success(
            provider="jira",
            pr_url="https://github.com/org/repo/pull/7",
            pr_title="fix",
            pr_body=body,
            skill_name="fix",
        )
        assert ">" not in result
        assert "[!" not in result
        assert "WARNING: Data loss possible" in result

    def test_success_github_branch_keeps_native_alert(self):
        body = "## Why\n> [!WARNING]\n> keep me native"
        result = build_pr_comment_success(
            provider="github",
            pr_url="https://github.com/org/repo/pull/8",
            pr_title="fix",
            pr_body=body,
        )
        assert "[!WARNING]" in result  # GitHub renders alerts natively

    def test_failure_reason_alert_flattened(self):
        result = build_pr_comment_failure(
            provider="jira",
            reason="> [!CAUTION]\n> merge conflict in core",
            skill_name="fix",
        )
        assert ">" not in result
        assert "[!" not in result
        assert "CAUTION: merge conflict in core" in result


class TestBypassPathsAlertFree(unittest.TestCase):
    def test_pr_submit_failure_body_is_alert_free(self):
        # pr_submit._post_issue_comment forwards build_pr_comment_failure()
        # output to upsert_jira_comment(); assert it carries no alert syntax.
        body = build_pr_comment_failure(
            provider="jira",
            reason="> [!IMPORTANT]\n> auth token expired",
            branch="koan/fix",
            skill_name="fix",
        )
        assert ">" not in body
        assert "[!" not in body
        assert "IMPORTANT: auth token expired" in body


if __name__ == "__main__":
    unittest.main()
