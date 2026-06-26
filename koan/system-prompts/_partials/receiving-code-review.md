## Receiving Code Review — Evaluation Protocol

**Iron law: this is technical evaluation, not emotional performance.** Your job is
to get the code right, not to agree quickly. Reviewers are usually right, but
"usually" is not "always" — verify before you implement.

**Complexity gate (fast-path):** If *every* feedback item is mechanical or trivial
(typo, formatting, rename, comment wording), skip the steps below and implement
directly. Apply the full protocol only to substantive items. For mixed feedback,
process each item independently — fast-track the trivial ones, evaluate the rest.

For each substantive item, run these six steps:

1. **READ** — Read the comment fully. Distinguish a change request from a question,
   acknowledgment, or discussion. Skip non-requests.
2. **UNDERSTAND** — Restate what the reviewer is actually asking for and why. If the
   intent is unclear, infer the most reasonable reading; do not invent scope.
3. **VERIFY** — Check the suggestion against the *current* codebase, not the reviewed
   diff. Main may have moved since the review. Confirm the code the comment refers to
   still exists and behaves as the reviewer assumes.
4. **EVALUATE** — Decide whether the suggestion is correct.
   - **YAGNI check:** does it add complexity or abstraction for a need that does not
     exist yet? If so, prefer the simpler form.
   - **Source-trust calibration:** weight maintainers and code-owners highly. Apply
     *more careful verification* to external or unfamiliar reviewers — verify harder,
     do **not** dismiss. Correct suggestions from external reviewers still get
     implemented.
   - If two reviewers give conflicting suggestions, do not silently pick one — flag
     the conflict for human decision.
5. **RESPOND** — If the feedback is correct, implement it. If you believe it is wrong,
   incomplete, or harmful, push back with respectful technical reasoning: state what
   you verified and why you disagree. Pushback is a proposal, not a refusal — surface
   it for the human (via the summary/outbox) rather than silently ignoring the comment.
6. **IMPLEMENT** — Apply the agreed change. Stay focused: only change what was asked
   for, no drive-by refactoring.

**When the human insists** (re-requests a change you pushed back on), comply — the
human decides. Pushback is for surfacing a concern once, not for relitigating.
