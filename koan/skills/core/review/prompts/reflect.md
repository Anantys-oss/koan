You are evaluating a set of code-review findings for quality and signal-to-noise ratio.

You will be given:
1. A JSON array of review findings (the "findings list")
2. The PR diff that was reviewed

Your task: for each finding, assign a score from 0 to 10 indicating how actionable, correct, and impactful it is.

**Scoring rubric:**

- **8-10 (high signal)**: Genuine bug, security issue, logic error, or meaningful architectural concern clearly visible in the diff.
- **5-7 (medium signal)**: Valid but minor: style, readability, missing test coverage, or improvement that would genuinely help.
- **3-4 (low signal)**: Vague, speculative, or context-dependent. The diff does not clearly support the finding.
- **0-2 (noise)**: The finding is wrong, refers to code not changed in the diff, misreads the context, suggests trivially cosmetic changes (add docstring, add type hint), or flags missing imports that are defined elsewhere.

**Score penalties:**
- Findings that assert facts about surrounding code without evidence (unverified claims): cap at 4
- Findings that describe what's wrong but not why it matters (no impact explanation): -2 from base score
- Findings with over-inflated severity (e.g. style issue marked critical): -3 from base score

**Common noise patterns to score 0-2:**
- Suggesting imports for symbols already defined in other files visible in the diff
- Recommending docstrings or type annotations on unchanged functions
- Pointing out style inconsistencies not introduced by this PR
- Flagging "missing error handling" on code paths that are already wrapped by callers
- Misidentifying test utilities as production code
- Generic advice not grounded in the specific diff ("consider adding tests")

**Findings list (JSON):**
```json
{FINDINGS_JSON}
```

**PR diff (may be truncated):**
```diff
{DIFF}
```

Respond with ONLY a JSON array — no prose, no markdown, no explanation outside the array. One entry per finding in the findings list:

```json
[
  {"finding_index": 0, "score": 7, "reason": "One-sentence justification."},
  ...
]
```

The array must have exactly one entry for each finding (indices 0 through N-1). Do not skip any index.
