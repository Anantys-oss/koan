You are compacting a learnings file for an autonomous coding agent. The learnings file contains bullet-point entries that the agent has accumulated over time from PR reviews, code analysis, and project experience.

Your job is to produce a shorter, higher-signal version of the learnings file by:

1. **Merging redundant entries**: If multiple entries say the same thing differently, combine them into one concise entry.
2. **Removing obsolete entries**: If an entry references a file, function, or pattern that no longer exists in the project (cross-reference with the file tree below), remove it. Only remove if the reference is specific enough to verify — general best practices should be kept.
3. **Consolidating by topic**: Group related entries together rather than keeping them in chronological order.
4. **Preserving high-signal entries**: Keep entries that are actionable, specific, and still relevant. Prefer entries that capture non-obvious insights over generic advice.

# Rules

- Output ONLY the compacted bullet list (lines starting with `- `), no headers or preamble
- NEVER invent new entries — only merge, remove, or rephrase existing ones
- Keep the total output around {MAX_LINES} content lines (soft target, not a hard limit)
- Preserve the exact meaning of entries you keep — do not generalize away specifics
- When merging entries, keep the most specific/actionable phrasing
- If an entry is ambiguous about whether it's still relevant, keep it

# Current Learnings

{LEARNINGS_CONTENT}

# Project File Tree (for cross-reference)

{FILE_TREE}
