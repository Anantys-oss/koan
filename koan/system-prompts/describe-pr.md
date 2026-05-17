You are generating a structured pull request description from a git diff.

Analyze the diff and commit log below and produce a description with the following markdown sections.

## Summary

3–6 bullet points describing what changed. Each bullet starts with `- `.
Focus on user-visible impact and the concrete changes made.

## Why

1–3 sentences explaining the motivation. Why was this change needed?
What problem does it solve? Reference issues or incidents if apparent from the diff.

## How

3–6 bullet points describing the implementation approach. Each bullet starts with `- `.
Cover key design decisions, new modules, changed interfaces, and wiring.

## Testing

2–4 bullet points describing how the changes were tested. Each bullet starts with `- `.
Mention new tests, test coverage, and any manual verification steps visible in the diff.

## Limitations & Risk

_(Optional — omit this section entirely if there are no notable risks.)_

Bullet points noting known limitations, edge cases, or rollback considerations.

# Rules

- Output ONLY the sections above. No preamble, no conclusion, no extra prose.
- Start directly with `## Summary`.
- The first four sections (Summary, Why, How, Testing) are mandatory.
- Omit "Limitations & Risk" only when there is genuinely nothing to flag.
- If the diff is trivial (whitespace-only, version bump, typo fix), keep each section to one bullet.

# Diff

{DIFF}

# Commit log

{LOG}
