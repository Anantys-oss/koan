You are analyzing PR review feedback to extract actionable lessons for an autonomous coding agent.

Below is review data from recent pull requests — including reviewer comments, approval/rejection states, and inline feedback. Your job is to distill this into concrete, actionable lessons the agent should remember.

# Instructions

- Extract specific, actionable lessons from the review feedback
- Each lesson should be a single markdown bullet point starting with `- `
- Focus on patterns that recur or carry strong signal (rejections, CHANGES_REQUESTED)
- Include both positive patterns (what reviewers value) and negative patterns (what to avoid)
- If a PR was closed without merge, explain why based on the review comments
- If reviewers mention specific files or areas to avoid, note them explicitly
- Write lessons in natural language — be concise but precise
- **Capture pushback outcomes.** When the agent disagreed with or pushed back on a
  reviewer's request, record whether that pushback was *validated* (the reviewer or
  human agreed / withdrew the request) or *overridden* (the human re-requested the
  same change and the agent should have complied). Phrase these as lessons, e.g.
  "Pushed back on X and the reviewer agreed — trust this evaluation pattern" or
  "Pushed back on Y but the human insisted — comply with this kind of request."
- Output ONLY the bullet list, no headers or preamble
- If there are no meaningful lessons to extract, output nothing

# Review Data

{REVIEW_DATA}
