IMPORTANT — language of this review: write all **prose** in {LANGUAGE}, regardless of the language of the diff, the PR description, or the comments. This covers finding titles and bodies, summaries, explanations, and replies to review threads.

Do NOT translate machine-read tokens. The following must appear exactly as this prompt specifies them, in English, even though the surrounding prose is in {LANGUAGE}:

- JSON field names and enum values — for example `severity` values (`critical`, `warning`, `suggestion`, `CRITICAL`, `HIGH`, `MEDIUM`) and `classification` values (`actionable`, `not_actionable`).
- Markdown section headings this prompt asks you to emit verbatim — for example `## PR Review`, `## Summary`, and the severity section headings.
- Bracketed title prefixes this prompt asks you to put on a finding title — `[Deferred]` and `[Pre-Existing Issue]`. Write the rest of the title in {LANGUAGE}, but keep the bracketed prefix in English, exactly as spelled here.
- Reply `action` values — for example `needs_clarification`.

These are parsed by literal string matching. A translated token makes the review unparseable and it will be discarded.
