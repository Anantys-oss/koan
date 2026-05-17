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

# Example output

## Summary

- Replaced ad-hoc PR description strings with a structured generation pipeline
- Added `describe_pr()` module that diffs branch, sends to Claude, and parses response
- Integrated auto-description into implement, fix, and rebase PR creation paths
- Graceful fallback: callers keep existing body when generation fails

## Why

PR descriptions were inconsistent free-form strings that made review harder. A structured format (what/why/how/testing) ensures every PR communicates the same baseline information, reducing reviewer friction.

## How

- Created `describe_pr.py` with `describe_pr()`, `_parse_description()`, and `format_description()`
- Prompt template in `system-prompts/describe-pr.md` defines section schema
- Wired into `implement_runner.py` and `fix_runner.py` before `submit_draft_pr()`
- `claude_step.py` prepends generated description to boilerplate in fallback path

## Testing

- 13 unit tests covering parser (clean output, leading prose, missing sections, empty input)
- Formatter tested for full rendering, missing optional sections, and empty dict
- `describe_pr()` tested for success, empty diff, CLI failure, and exception paths
- Full test suite passes with no regressions

## Limitations & Risk

- Truncates diffs over 32k characters — very large PRs may get incomplete descriptions
- Depends on Claude availability; fallback body is used when generation fails

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
