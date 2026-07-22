## Comprehensive Discovery (thorough multi-perspective pass)

Review this diff comprehensively from a **fixed set of independent perspectives**,
so a single review surfaces as many genuine issues as possible and a later review
has little to add. Cover each perspective deliberately before writing findings:

1. **Correctness** — logic bugs, off-by-one, null/None, edge cases, wrong results.
2. **Security** — injection, auth/authz gaps, secrets, unsafe input handling.
3. **Architecture** — coupling, layering, abstraction level, misplaced responsibility.
4. **Silent failure** — swallowed errors, ignored return values, empty excepts, gaps
   where a failure would pass unnoticed.
5. **Test coverage** — new/changed behavior with no corresponding test.

Where your tooling allows, you may delegate perspectives to sub-agents and gather
their results; otherwise reason through each perspective yourself.

Then **merge into one set**: if two perspectives surface the **same underlying
issue** (same location and topic), report it **once**, keeping the highest justified
severity and the clearest explanation — do not emit duplicates. Apply the same
severity calibration and verdict rules as always: comprehensive discovery changes
*how many* real issues you find, never the bar for what counts as blocking.

This is best-effort and bounded: if a perspective yields nothing, move on; never
invent issues to fill a perspective. If the diff is large and only partially
covered, review what is present and rely on the standard partial-coverage
reporting — do not silently imply full coverage.
