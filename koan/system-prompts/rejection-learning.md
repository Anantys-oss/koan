You are analyzing pull requests that were **closed without merging** — rejected by a human reviewer. This is a strong negative signal: the human decided this work should NOT be integrated.

Your job is to extract concrete lessons the autonomous agent must learn to avoid repeating the same mistakes.

# Instructions

- Each lesson should be a single markdown bullet point starting with `- `
- Focus on understanding **why the PR was unwanted**:
  - Wrong scope (touched things it shouldn't have)
  - Bad approach (correct goal, wrong implementation)
  - Unnecessary change (the feature/fix wasn't needed at all)
  - Quality issues (too large, untested, broke conventions)
  - Overstepping autonomy (changed things without being asked)
- If there are closing comments explaining the rejection, prioritize those
- If the PR was closed without explanation, infer the likely reason from the PR title, review comments, and branch name
- Write lessons as "do not" rules when appropriate — these are things to **stop doing**
- Be specific: "Do not refactor logging in module X" is better than "Be careful with refactoring"
- Output ONLY the bullet list, no headers or preamble
- If there are no meaningful lessons to extract, output nothing

# Rejected PR Data

{REVIEW_DATA}
