"""Tests for tracker_comment_format.py — Jira-specific branches and helpers."""

import os
import sys
import unittest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tracker_comment_format import (
    build_plan_comment_failure,
    build_plan_comment_success,
    build_pr_comment_failure,
    build_pr_comment_success,
    jira_readable_markdown,
)


# ---------------------------------------------------------------------------
# _strip_markdown_for_jira (via jira_readable_markdown)
# ---------------------------------------------------------------------------


class TestStripMarkdownForJira(unittest.TestCase):
    def test_empty_input(self):
        assert jira_readable_markdown("") == ""
        assert jira_readable_markdown(None) == ""

    def test_headings_stripped(self):
        result = jira_readable_markdown("## Summary\nSome text")
        assert "##" not in result
        assert "Summary" in result
        assert "Some text" in result

    def test_links_converted(self):
        result = jira_readable_markdown("[click here](https://example.com)")
        assert result == "click here (https://example.com)"

    def test_inline_code_stripped(self):
        result = jira_readable_markdown("Use `foo()` to do it")
        assert result == "Use foo() to do it"

    def test_bold_stripped(self):
        result = jira_readable_markdown("This is **bold** and __also bold__")
        assert result == "This is bold and also bold"

    def test_ordered_list_becomes_bullets(self):
        result = jira_readable_markdown("1. First\n2. Second")
        assert "- First" in result
        assert "- Second" in result
        assert "1." not in result

    def test_hr_stripped(self):
        result = jira_readable_markdown("Above\n---\nBelow")
        assert "---" not in result
        assert "Above" in result
        assert "Below" in result

    def test_fenced_code_block_indented(self):
        md = "Before\n```\nprint('hello')\n```\nAfter"
        result = jira_readable_markdown(md)
        assert "    print('hello')" in result
        assert "```" not in result
        assert "Before" in result
        assert "After" in result

    def test_excessive_blank_lines_collapsed(self):
        md = "A\n\n\n\n\nB"
        result = jira_readable_markdown(md)
        assert "\n\n\n" not in result
        assert "A\n\nB" == result

    def test_crlf_normalized(self):
        result = jira_readable_markdown("A\r\nB\rC")
        assert "\r" not in result
        assert "A" in result and "B" in result and "C" in result


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
        assert "Koan update: Draft pull request created." in result

    def test_mission_from_skill_name(self):
        result = self._call(skill_name="implement")
        assert "Mission: /implement" in result

    def test_unknown_mission_when_empty_skill(self):
        result = self._call(skill_name="")
        assert "Mission: (unknown)" in result

    def test_pr_url_included(self):
        result = self._call()
        assert "https://github.com/org/repo/pull/42" in result

    def test_pr_title_included(self):
        result = self._call(pr_title="fix: add index")
        assert "PR title: fix: add index" in result

    def test_pr_title_omitted_when_empty(self):
        result = self._call(pr_title="")
        assert "PR title:" not in result

    def test_target_branch_included(self):
        result = self._call(base_branch="develop")
        assert "Target branch: develop" in result

    def test_target_branch_omitted_when_none(self):
        result = self._call(base_branch=None)
        assert "Target branch:" not in result

    def test_what_section_from_summary(self):
        body = "## Summary\n- Added foo\n- Fixed bar"
        result = self._call(pr_body=body)
        assert "What changed:" in result
        assert "- Added foo" in result
        assert "- Fixed bar" in result

    def test_what_section_from_changes(self):
        body = "## Changes\n- Rewrote module"
        result = self._call(pr_body=body)
        assert "What changed:" in result
        assert "- Rewrote module" in result

    def test_why_section(self):
        body = "## Why\nPerformance regression."
        result = self._call(pr_body=body)
        assert "Why: Performance regression." in result

    def test_how_section(self):
        body = "## How\n- Used caching\n- Added index"
        result = self._call(pr_body=body)
        assert "How it was implemented:" in result
        assert "- Used caching" in result

    def test_testing_section(self):
        body = "## Testing\n- Unit tests added\n- Manual QA"
        result = self._call(pr_body=body)
        assert "Validation:" in result
        assert "- Unit tests added" in result

    def test_bullets_capped_at_eight(self):
        items = "\n".join(f"- item {i}" for i in range(12))
        body = f"## Summary\n{items}"
        result = self._call(pr_body=body)
        assert "- item 7" in result
        assert "- item 8" not in result

    def test_next_section_present(self):
        result = self._call()
        assert "Next:" in result
        assert "Review the draft PR and merge when ready." in result

    def test_no_markdown_formatting(self):
        result = self._call(pr_body="## Summary\n- stuff")
        assert "##" not in result
        assert "```" not in result

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
        assert "Koan update: Pull request creation failed." in result

    def test_mission_name(self):
        result = self._call(skill_name="review")
        assert "Mission: /review" in result

    def test_unknown_mission(self):
        result = self._call(skill_name="")
        assert "Mission: (unknown)" in result

    def test_reason_included(self):
        result = self._call(reason="Rate limit exceeded")
        assert "Reason: Rate limit exceeded" in result

    def test_reason_defaults_on_empty(self):
        result = self._call(reason="")
        assert "Reason: Unknown error" in result

    def test_branch_included(self):
        result = self._call(branch="koan/fix-auth")
        assert "Current branch: koan/fix-auth" in result

    def test_branch_omitted_when_empty(self):
        result = self._call(branch="")
        assert "Current branch:" not in result

    def test_target_branch_included(self):
        result = self._call(base_branch="main")
        assert "Target branch: main" in result

    def test_target_branch_omitted_when_none(self):
        result = self._call(base_branch=None)
        assert "Target branch:" not in result

    def test_next_steps_present(self):
        result = self._call()
        assert "Next:" in result
        assert "Check branch state and repository permissions." in result
        assert "Re-run the mission after fixing the blocking issue." in result

    def test_no_markdown_formatting(self):
        result = self._call()
        assert "##" not in result
        assert "`" not in result

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
        assert "Koan plan update" in result
        assert "Title: Plan: Widget Revamp" in result
        assert "Generated by Koan." in result

    def test_body_stripped_of_markdown(self):
        result = build_plan_comment_success(
            "jira", "Plan", "## Overview\n**Bold text** and `code`"
        )
        assert "##" not in result
        assert "**" not in result
        assert "`" not in result
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


if __name__ == "__main__":
    unittest.main()
