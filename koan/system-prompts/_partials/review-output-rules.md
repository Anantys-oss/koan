- **file_comments**: Array of per-file inline comments. Empty array `[]` if no issues found.
- **file**: File path as shown in the diff (e.g. `src/auth.py`).
- **line_start** / **line_end**: Line numbers in the **new (post-change) file as it appears at the PR head** — i.e. the line numbers on the added/`+` side of the diff, not diff-relative offsets. These are used to build a direct link into the file, so they must point at the real lines in the updated file. Same value for single-line issues. Use `0` for whole-file comments.
- **severity**: Must be exactly one of: `"critical"` (blocking, must fix), `"warning"` (important, should fix), `"suggestion"` (nice to have).
- **title**: Short title for the issue.
- **comment**: Detailed explanation with suggested fix. Structure as: what's wrong → why it matters (real-world impact) → how to fix. Use markdown for readability: separate distinct thoughts into short paragraphs (blank line between them) and use `-` bullet points when listing more than one item. Avoid one dense block of text.
- **code_snippet**: Relevant code illustrating the issue. Empty string `""` if not needed.
- **lgtm**: `true` if the PR is merge-ready with no blocking issues, `false` otherwise.
- **summary**: Final assessment — what's good, what needs fixing, merge readiness. Format for readability, not as a single dense paragraph: lead with a one-line verdict (the TL;DR), then a blank line, then specific strengths of the PR (name concrete things done well, not generic praise), then a blank line, then a short `-` bullet list of the key issues (one bullet per distinct finding). Markdown is rendered, so use `\n\n` between blocks and `\n` between bullets. A reader should be able to skim the bullets and grasp every point without re-reading.
- **checklist**: Review checklist results. Empty array `[]` for trivial changes. Each item has `passed` (bool) and `finding_ref` (cross-reference like `"critical #1"`, or empty string `""` if passed).

All fields in `file_comments` and `review_summary` are required. Use empty strings `""`, empty arrays `[]`, or `false` as sentinel values — never omit a field.
- **comment_replies**: Optional. Array of replies to user comments. Omit or use `[]` if no replies are warranted. Each item needs `comment_id` (integer, from the repliable comments list), `reply` (string, concise and actionable, 2-4 sentences max), and `action` (string, optional — one of: `"fixed"` if you changed code to address it, `"wont_fix"` if dismissing with a reason, `"needs_clarification"` if you need more info from the reviewer, `"acknowledged"` otherwise; defaults to `"acknowledged"` if omitted).
- **close_pr**: Optional. Object signalling whether to close the PR after the review is posted. `close` (bool) defaults to `false`. `reason` (string) is a short closure rationale, empty when `close=false`. Omit the field entirely if not closing — only include it when `close=true`.

IMPORTANT: Output ONLY the JSON object. No markdown formatting, no explanatory text, no code fences around the JSON.
