# PR Explanation

You are explaining a pull request to a human who wants to deeply understand
the intent, the problem, and the solution — in plain, simple language.

## Pull Request: {TITLE}

**Author**: @{AUTHOR}
**Branch**: `{BRANCH}` → `{BASE}`

### PR Description

{BODY}
{PROJECT_MEMORY}
---

## Current Diff

```diff
{DIFF}
```

---

## Existing Discussion

{REVIEWS}

{REVIEW_COMMENTS}

{ISSUE_COMMENTS}

---

## Your Task

Produce a clear, pedagogical explanation of this PR. Write for someone who
knows the language but may not know this codebase. Use everyday words — no
jargon without immediate explanation.

### Structure your explanation as follows:

#### 1. The Problem (What was wrong?)

- Describe the situation BEFORE this PR in concrete terms
- Use a **specific example** showing the problematic behavior:
  what input/action leads to what bad outcome
- Explain WHY it's a problem (user impact, data loss, performance, etc.)

#### 2. The Solution (How does this PR fix it?)

- Walk through the key changes step by step, in logical order
- For each change:
  - **What** changed (file, function, component)
  - **Why** this specific change was needed
  - **How** it connects to the overall fix
- Use bullet lists — one idea per bullet, keep each bullet short
- Include a **before/after example** showing the improved behavior

#### 3. How It Works (The mechanism)

- Explain the workflow/data flow after the change
- Walk through a concrete scenario end-to-end
- Highlight any edge cases the PR handles

#### 4. Could It Be Simpler? (Critical analysis)

Based on your knowledge of the codebase, challenge whether a simpler
approach could have worked:

- Are there existing utilities or patterns that could have been reused?
- Could the same result be achieved with fewer changes?
- Are there trade-offs in this approach worth noting?
- If the approach IS the simplest viable option, say so and explain why

### Formatting Rules

- Use **rich markdown**: headers, bold for emphasis, code blocks for
  identifiers and snippets, bullet lists for decomposition
- Keep paragraphs short (2-3 sentences max)
- Use `inline code` for function names, file paths, and variable names
- When referencing a file change, mention the file path
- Concrete examples beat abstract descriptions — always illustrate

### Tone

- Direct and clear, like explaining to a smart colleague
- No hedging ("it seems", "it appears") — be confident
- If something is unclear from the diff, say so explicitly
