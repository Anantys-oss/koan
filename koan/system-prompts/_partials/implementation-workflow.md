### Phase 3 — Test First (when possible)

7. **Write tests that reproduce the issue** before fixing it. Follow existing test patterns (pytest, `tests/test_*.py`). The tests should FAIL before the fix.
{@include test-guidance}
8. If the issue cannot be reproduced in tests (infrastructure, config, etc.), note why and skip this step.

### Phase 4 — Implement (repeat per phase)

For each phase in your plan:

9. **Create a feature branch — mandatory** (first phase only). If you are currently on the base branch `{BASE_BRANCH}` (or on `main` / `master`), create the feature branch now using the naming specified earlier in this prompt and switch to it before the first edit. **Never commit on the base branch.** If you are already on a feature branch (anything other than `{BASE_BRANCH}`, `main`, or `master`), stay on it.
10. **Implement the change.** Edit the minimal set of files needed. Follow project conventions strictly.
11. **Run tests** to verify. Fix any failures before proceeding.
12. **Commit** with a clear message describing what this phase does.

### Phase 5 — Quality Cycle (per commit)

After each commit:

13. **Refactor**: If a refactor skill is available, invoke it and apply suggestions. Amend.
14. **Review**: If a review skill is available, invoke it and apply fixes for issues rated medium or higher. Amend.

### Phase 6 — Final Verification

15. **Run the full relevant test suite** to ensure no regressions.
16. **Verify all items** are addressed.

### Phase 7 — Push Branch

17. **Push the branch** to origin:
    ```bash
    git push -u origin HEAD
    ```

18. **Do not create a pull request**. Kōan creates the draft PR automatically after the session using the pushed branch, commit subjects, diff, test notes, and tracker context.
{@include pr-submit-fork}
