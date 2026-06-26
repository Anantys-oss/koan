# PR Review — Address Feedback

You are reviewing a pull request and implementing the changes requested by reviewers.

## Pull Request: {TITLE}

**Branch**: `{BRANCH}` → `{BASE}`

### PR Description

{BODY}

---

## Current Diff

```diff
{DIFF}
```

---

## Review Comments (inline on code)

{REVIEW_COMMENTS}

## Reviews (top-level)

{REVIEWS}

## Conversation Thread

{ISSUE_COMMENTS}

---

{@include receiving-code-review}

---

## Your Task

1. **Process the review feedback through the protocol above.** Run each substantive
   comment through READ→UNDERSTAND→VERIFY→EVALUATE→RESPOND→IMPLEMENT; fast-path
   trivial items.
2. **Implement the changes you agree with.** Edit the code to address each correct
   review comment.
   - If a comment is a question or discussion (not a change request), skip it.
   - If a comment requests a change you believe is wrong or would break functionality,
     do **not** blindly implement it — push back with technical reasoning per the
     RESPOND step and note your concern in the summary. Implement the rest.
3. **Run the test suite** to make sure your changes don't break anything.
   - Look for a Makefile, package.json, or similar to find the test command.
   - If tests fail, fix them.
4. **Be thorough but focused.** Only change what reviewers asked for — no drive-by refactoring.

When you're done, output a concise summary of what you changed and why, including any
feedback you pushed back on and the reasoning.
