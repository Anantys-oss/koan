You are analyzing whether a {KIND} is still needed given the current state of the repository.

## Target

**{KIND} #{NUMBER}: {TITLE}** in `{REPO}`

### Description
{BODY}

### Changed files (if PR)
{DIFF_SUMMARY}

### Full diff (if PR)
{FULL_DIFF}

### Recent discussion
{COMMENTS}

## Instructions

Investigate the **current state** of the repository's main branch to determine whether this
{KIND} is still needed, partially needed, or fully superseded by recent changes.

### Investigation steps

1. **Read the current codebase** — use Read, Glob, and Grep to explore the areas of code
   that this {KIND} targets. Focus on the files and functions mentioned in the diff or description.

2. **Compare against recent main branch changes** — look for:
   - Code that was added to main that already addresses the same concern
   - Refactors that removed or restructured the code this {KIND} touches
   - New features or fixes that make this change unnecessary
   - Architectural changes that conflict with the approach taken here

3. **Evaluate relevance** — for each change or concern in the {KIND}:
   - Does the problem still exist in main?
   - Has the problem been solved differently?
   - Is the proposed solution still compatible with current code?
   - Would the change still provide value even if partially overlapping?

### Output format

Your response will be posted as a GitHub comment. Format it as follows:

## Relevance Analysis

### Verdict: [Still Needed / Partially Needed / No Longer Needed / Needs Adaptation]

[1-2 sentence executive summary]

### Detailed Analysis

For each significant change or concern, provide:

- **[Area/File/Feature]**: [Still needed | Superseded | Partially addressed]
  - *Current state*: [what exists now in main]
  - *This {KIND}*: [what it proposes/changes]
  - *Assessment*: [why it's still needed or not]

### Key Advantages (if still needed)

If the {KIND} is still relevant, list the main advantages of merging/implementing it:

1. **[Advantage]** — [explanation]
2. **[Advantage]** — [explanation]

### Risks or Conflicts (if any)

- [Any merge conflicts, architectural mismatches, or concerns]

### Recommendation

[Clear recommendation: merge as-is, update and merge, close, or keep open with modifications]

---

Guidelines:
- Be precise and evidence-based — cite specific file paths and line numbers
- Compare actual code, not just descriptions
- If you find the changes are still valuable, explain WHY in detail
- If superseded, show exactly WHAT superseded them (commit, PR, or code change)
- Use markdown formatting appropriate for GitHub
- Do NOT include greetings, sign-offs, or meta-commentary about being an AI
