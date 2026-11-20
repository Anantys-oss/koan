"""Tests for folding the agent-facing half of a plan comment."""

import os
import re
import sys
import unittest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tracker_comment_format import (  # noqa: E402
    _PLAN_FOLD_LABEL,
    build_plan_comment_success,
    fold_plan_agent_sections,
    fold_plan_for_tracker,
)

PLAN = """### Plan Assessment

**Complexity:** M · **Risk:** Low · **Why:** one isolated text transform

### Summary

What this achieves.

### Alternatives Considered

- **A (chosen)**: do it.

### File Map

| Action | File |
|---|---|
| Modify | `a.py` |

### Implementation Phases

#### Phase 1: First

- [ ] **Step 1** — go
  <details><summary>Test code</summary>

  ```python
  assert True
  ```

  </details>

#### Phase 2: Second

- [ ] **Step 1** — go

### Corner Cases

- Empty input.

### Testing Strategy

Unit tests.

### Risks & Alternatives

None.

### Verification Criteria

- Given a plan, when posted, then it folds.

### Open Questions

- Should we also fold the summary?
"""


class TestFoldPlanAgentSections(unittest.TestCase):
    def test_human_sections_stay_visible_and_ordered(self):
        folded = fold_plan_agent_sections(PLAN)
        visible = folded.split("<details>\n<summary>")[0]
        self.assertIn("**Complexity:** M", visible)
        self.assertIn("**Phases:** 2", visible)
        self.assertIn("### Summary", visible)
        self.assertIn("### Open Questions", visible)
        self.assertIn("### Verification Criteria", visible)
        self.assertLess(visible.index("### Summary"), visible.index("### Open Questions"))
        self.assertLess(
            visible.index("### Open Questions"), visible.index("### Verification Criteria")
        )

    def test_agent_sections_are_folded_once_in_original_order(self):
        folded = fold_plan_agent_sections(PLAN)
        self.assertEqual(folded.count(f"<summary>{_PLAN_FOLD_LABEL}</summary>"), 1)
        hidden = folded.split(f"<summary>{_PLAN_FOLD_LABEL}</summary>", 1)[1]
        for heading in (
            "### Alternatives Considered",
            "### File Map",
            "### Implementation Phases",
            "### Corner Cases",
            "### Testing Strategy",
            "### Risks & Alternatives",
        ):
            self.assertIn(heading, hidden)
        positions = [
            hidden.index(h)
            for h in ("### Alternatives Considered", "### File Map", "### Corner Cases")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_nested_details_survive(self):
        folded = fold_plan_agent_sections(PLAN)
        self.assertIn("<details><summary>Test code</summary>", folded)
        self.assertIn("assert True", folded)

    def test_implement_marker_still_matches(self):
        marker = re.compile(
            r"^#{2,}\s+(?:Implementation Phases|Phase \d+|Summary|Changes in this iteration)",
            re.MULTILINE | re.IGNORECASE,
        )
        self.assertTrue(marker.search(fold_plan_agent_sections(PLAN)))

    def test_idempotent(self):
        once = fold_plan_agent_sections(PLAN)
        self.assertEqual(fold_plan_agent_sections(once), once)

    def test_no_summary_heading_is_left_untouched(self):
        body = "### Notes\n\nsomething\n"
        self.assertEqual(fold_plan_agent_sections(body), body)

    def test_empty_body(self):
        self.assertEqual(fold_plan_agent_sections(""), "")

    def test_human_only_plan_is_not_wrapped(self):
        body = "### Summary\n\nShort.\n\n### Open Questions\n\nNone.\n"
        folded = fold_plan_agent_sections(body)
        self.assertNotIn("<details>", folded)
        self.assertIn("### Summary", folded)
        self.assertIn("### Open Questions", folded)

    def test_missing_assessment_section_degrades(self):
        body = PLAN.split("### Summary", 1)[1]
        folded = fold_plan_agent_sections("### Summary" + body)
        self.assertIn("<details>", folded)
        self.assertNotIn("**Complexity:**", folded)

    def test_preamble_before_first_heading_is_kept(self):
        folded = fold_plan_agent_sections("Intro line.\n\n" + PLAN)
        self.assertTrue(folded.startswith("Intro line."))

    def test_heading_inside_code_fence_is_not_a_section(self):
        fenced = (
            "### File Map\n\n```markdown\n### Summary\n\nfake\n```\n\n"
            "```python\n# comment\n### Open Questions\n```\n\n"
        )
        folded = fold_plan_agent_sections(PLAN.replace("### File Map\n\n", fenced))
        visible, hidden = folded.split(f"<summary>{_PLAN_FOLD_LABEL}</summary>", 1)
        self.assertIn("What this achieves.", visible)
        self.assertNotIn("fake", visible)
        self.assertIn("```markdown\n### Summary\n\nfake\n```", hidden)
        self.assertIn("```python\n# comment\n### Open Questions\n```", hidden)
        self.assertEqual(folded.count("<details>"), folded.count("</details>"))

    def test_duplicate_human_section_is_not_dropped(self):
        body = PLAN + "\n### Summary\n\nSecond summary.\n"
        folded = fold_plan_agent_sections(body)
        self.assertIn("What this achieves.", folded)
        self.assertIn("Second summary.", folded)

    def test_assessment_in_inline_backticks_renders_as_markdown(self):
        line = "**Complexity:** M · **Risk:** Low · **Why:** one isolated text transform"
        folded = fold_plan_agent_sections(PLAN.replace(line, f"`{line}`"))
        self.assertIn(f"{line} · **Phases:** 2", folded)
        self.assertNotIn(f"`{line}", folded)

    def test_assessment_in_code_fence_renders_as_markdown(self):
        line = "**Complexity:** M · **Risk:** Low · **Why:** one isolated text transform"
        folded = fold_plan_agent_sections(PLAN.replace(line, f"```\n{line}\n```"))
        self.assertTrue(folded.startswith(f"{line} · **Phases:** 2\n"))

    def test_changes_in_this_iteration_stays_visible(self):
        body = PLAN.replace(
            "### Summary", "### Changes in this iteration\n\n- Addressed feedback.\n\n### Summary"
        )
        visible = fold_plan_agent_sections(body).split("<details>\n<summary>")[0]
        self.assertIn("- Addressed feedback.", visible)
        self.assertLess(
            visible.index("### Changes in this iteration"), visible.index("### Summary")
        )

    def test_h2_level_plan(self):
        folded = fold_plan_agent_sections(PLAN.replace("### ", "## "))
        self.assertIn("<details>", folded)
        self.assertIn("## Summary", folded.split("<details>")[0])


class TestFoldPlanForTracker(unittest.TestCase):
    def test_jira_is_untouched(self):
        self.assertEqual(fold_plan_for_tracker("jira", PLAN), PLAN)

    def test_github_is_folded(self):
        self.assertIn("<details>", fold_plan_for_tracker("github", PLAN))


class TestBuildPlanCommentSuccess(unittest.TestCase):
    def test_github_comment_is_folded(self):
        comment = build_plan_comment_success("github", "A title", PLAN)
        self.assertIn("## A title", comment)
        self.assertIn(f"<summary>{_PLAN_FOLD_LABEL}</summary>", comment)

    def test_jira_comment_is_not_folded(self):
        comment = build_plan_comment_success("jira", "A title", PLAN)
        self.assertNotIn(_PLAN_FOLD_LABEL, comment)


if __name__ == "__main__":
    unittest.main()
