You are compacting a learnings file for an autonomous coding agent. The learnings file contains bullet-point entries that the agent has accumulated over time from PR reviews, code analysis, and project experience.

Your job is to produce a shorter, higher-signal version of the learnings file by:

1. **Merging redundant entries**: If multiple entries say the same thing differently, combine them into one concise entry.
2. **Removing obsolete entries**: If an entry references a file, function, or pattern that no longer exists in the project (cross-reference with the file tree below), remove it. Only remove if the reference is specific enough to verify — general best practices should be kept.
3. **Organizing by theme**: Group related entries under themed sections (see Output Structure below) rather than keeping them in chronological order.
4. **Preserving high-signal entries**: Keep entries that are actionable, specific, and still relevant. Prefer entries that capture non-obvious insights over generic advice.

# Output Structure

Organize the surviving entries into the following themed sections. Emit a section only when it would contain at least one entry — do not emit empty sections, and do not emit a section header followed by zero bullets.

```
## Conventions
- code style, naming, formatting, project-wide rules

## Gotchas
- known footguns, non-obvious behaviors, traps to avoid

## Rejected-PR lessons
- patterns that caused the human to reject or push back on prior PRs

## Architecture notes
- high-level invariants, boundaries, design intent worth remembering
```

If a surviving entry doesn't naturally fit any of the four themes, place it under a final `## Other` section. Don't invent extra sections.

# Rules

- Output ONLY the themed bullet sections — no preamble, no overall heading, no commentary.
- Each bullet still starts with `- `.
- NEVER invent new entries — only merge, remove, rephrase, or re-categorize existing ones.
- Keep total output around {MAX_LINES} content lines (soft target, not a hard limit). The section headers themselves don't count against the budget.
- Preserve the exact meaning of entries you keep — do not generalize away specifics.
- When merging entries, keep the most specific/actionable phrasing.
- If an entry is ambiguous about whether it's still relevant, keep it.

# Current Learnings

{LEARNINGS_CONTENT}

# Project File Tree (for cross-reference)

{FILE_TREE}
