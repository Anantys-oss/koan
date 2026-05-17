You are filtering a fresh batch of lesson candidates before they are appended to an autonomous coding agent's project learnings file.

The agent has *already extracted* the candidate lessons from recent PR reviews. Your only job is to **drop any candidate that says the same thing as something already in the existing learnings**, even if the wording is different. This prevents the file from accumulating paraphrased duplicates.

# Rules

- Output ONLY the surviving bullet list (lines starting with `- `), no headers, no commentary, no preamble.
- A candidate is a duplicate when an existing entry conveys the same actionable rule, even with different wording (e.g. "test PR changes" ≈ "verify changes with tests").
- A candidate is NOT a duplicate when it adds a new specific (file path, function name, edge case, threshold, or counter-example) that the existing entries lack.
- When in doubt, keep the candidate — the periodic semantic compaction pass will merge it later.
- Preserve the exact wording of surviving candidates. Do NOT rewrite, generalize, or "improve" them.
- If every candidate is a duplicate, output nothing (empty string).

# Existing learnings (do not output)

{EXISTING_CONTENT}

# Candidate lessons (filter these)

{NEW_LESSONS}
