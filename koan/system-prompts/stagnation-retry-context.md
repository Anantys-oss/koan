# Stagnation Recovery Context

**This mission has been attempted {RETRY_COUNT} time(s) before and stagnated each time.**

Previous stagnation pattern: **{PATTERN_TYPE}**
Last observed output before abort:
```
{SAMPLE_LINES}
```

You MUST try a fundamentally different approach than what was attempted before. Specifically:
- If the pattern was `tool_loop`: avoid repeating the same tool call in a loop. Break the problem into smaller steps.
- If the pattern was `infinite_retry`: the error is likely unfixable with the same strategy. Read the error carefully and try an alternative.
- If the pattern was `interactive_wait`: do not invoke commands that require interactive input. Use non-interactive flags or alternatives.
- If the pattern was `silent` or `unknown`: start with a diagnostic step — read the relevant files before making changes.
