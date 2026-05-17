You are generating a structured pull request description from a git diff.

Analyze the diff and log below and produce a description with exactly these three markdown sections:

## Type

One of: `bug fix`, `enhancement`, `docs`, `tests`, `refactor`, `chore`

## Summary

3–6 bullet points describing what changed and why. Each bullet starts with `- `.
Focus on intent and user-visible impact, not implementation minutiae.

## Walkthrough

A bullet for each changed file (or logical group of files) in the format:
- `path/to/file.py` — one-sentence description of what changed

Limit to the most significant files (max 10). Skip generated files, lock files, and changelogs.

# Rules

- Output ONLY the three sections above. No preamble, no conclusion, no extra prose.
- Start directly with `## Type`.
- If the diff is trivial (whitespace-only, version bump, typo fix), write one bullet in Summary and skip Walkthrough.

# Diff

{DIFF}

# Commit log

{LOG}
