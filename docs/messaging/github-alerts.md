---
type: doc
title: "GitHub alert callouts"
description: "How to emit GitHub alert callouts (NOTE/TIP/IMPORTANT/WARNING/CAUTION) via build_alert(), when to reach for each type, and the parsimony rule."
tags: [messaging]
created: 2026-07-10
updated: 2026-07-10
---

# GitHub alert callouts

Use `koan/app/github_alerts.build_alert(kind, text, provider="github")` to emit
a GitHub alert. It handles `> ` prefixing (including blank-line joining) and
degrades to `KIND: text` on non-GitHub trackers (e.g. Jira).

```python
from app.github_alerts import build_alert

parts.append(
    build_alert(
        "WARNING",
        "**Review feedback was NOT applied.** Re-run `/rebase` or apply manually.",
    )
    + "\n"  # caller owns the surrounding whitespace
)
```

- **Kinds:** NOTE, TIP, IMPORTANT, WARNING, CAUTION (case-insensitive; any other
  value raises `ValueError`).
- The helper adds **no** surrounding blank lines — you control spacing around the
  block.
- Multi-line bodies are prefixed on every line; a blank line becomes a bare `>`.
- On a non-GitHub provider the block degrades to `KIND: text`.

## When to use each type

| Type | Use for |
|------|---------|
| NOTE | Neutral context the reader should not miss. |
| TIP | Optional advice / a better way to do something. |
| IMPORTANT | Info critical to success (e.g. "the branch moved during review"). |
| WARNING | Needs immediate attention; risk of a bad outcome. |
| CAUTION | Risk / negative consequence of an action. |

The project's QUESTION / TODO / BUG / SUCCESS / ERROR conventions have no
GitHub-native callout — write those as emoji + bold in prose, not via
`build_alert()`.

## Parsimony

**≤ 1–2 alerts per comment. Never one per finding.** A wall of callouts defeats
the "un-missable" purpose. Reserve alerts for the single most important thing the
reader must not miss.

See `specs/components/comment-formatting.md` for the type→situation contract.
