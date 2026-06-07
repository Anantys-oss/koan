You are compacting a learnings file for an autonomous coding agent. The learnings file contains bullet-point entries that the agent has accumulated over time from PR reviews, code analysis, and project experience.

Your job is to produce a shorter, higher-signal version of the learnings file by:

1. **Merging redundant entries**: If multiple entries say the same thing differently, combine them into one concise entry.
2. **Removing bug observations**: Remove entries that describe a specific defect state rather than a durable rule. Apply this test: "Is this still true if the bug were fixed?" If no, discard it — bug-specific details belong in commit messages, not persistent memory. Examples to drop: "function X returns None when Y", "workaround needed until PR #N is merged", "endpoint Z 500s on empty payload". Counter-example to KEEP — a durable config invariant like "stagnation monitor ignores outputs under its min-bytes threshold" is a rule, not a bug observation.
3. **Removing obsolete entries**: If an entry references a file, function, or pattern that no longer exists in the project (cross-reference with the file tree below), remove it. Only remove if the reference is specific enough to verify — general best practices should be kept.
4. **Organizing by theme**: Group related entries under themed sections (see Output Structure below) rather than keeping them in chronological order.
5. **Preserving high-signal entries**: Keep entries that are actionable, specific, and still relevant. Prefer entries that capture non-obvious insights over generic advice. Only keep an entry if it describes a rule, convention, pattern, or architectural decision that remains true regardless of any specific bug.

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
