---
title: "Review consistency, triage & human dispositions"
description: "Why /review is stable across re-runs, how the yellow-tier bar and pre-existing labeling work, and the deliberate 'human decides' posture (and its injection tradeoff) for honoring PR-comment dispositions."
tags: [design, review, decision]
created: 2026-07-22
updated: 2026-07-22
---

# Review consistency, triage & human dispositions (spec 010)

/ review used to feel like a slot machine: re-running it surfaced a different set of
issues each time, and the yellow "Important" bucket was over-inclusive, blocking
mergeable PRs on nits. Spec 010 addresses this. The durable contract lives in
`specs/skills/review.md` (§ "Consistency, triage & human dispositions"); this page
records the *why* and the load-bearing tradeoffs.

## The two problems

1. **Drift / whiplash.** Findings are re-derived by the model each run, so a re-review
   of unchanged code returned a different set — and worse, after an author fixed the
   reported issues and pushed, the next review raised *new* issues on code that had not
   changed. Those issues either should have been caught the first time (if important) or
   left alone (if not) — surfacing them late is pure noise.
2. **Over-inclusive yellow tier.** `warning` is a *blocking* severity, but borderline
   "should-fix" items landed there, turning merge-ready PRs into request-changes.

## Design

- **Determinism where it counts.** The consistency-critical decisions are made in
  deterministic Python, not model output: finding identity (`review_identity`), the
  reuse decision (`review_reuse`), the re-review freeze (`review_reconcile`), and the
  severity/label enforcement (`review_triage`). The model's qualitative judgment (what
  is a bug, what is borderline, what predates the changeset) stays in the prompt; the
  mechanical guarantees (verdict follows severity, `[Deferred]`/`[Pre-Existing Issue]`
  demotion, dedup identity) are Python.
- **Reuse + freeze.** Unchanged head+base+request → reproduce the prior review. Changed
  → re-derive but freeze first-time non-critical findings on files unchanged since the
  prior review (a `critical` still surfaces, labeled). This is the direct fix for the
  fix-and-push whiplash.
- **Prompt-fixed bar.** The yellow bar and the `[Pre-Existing Issue]` label are fixed in
  the `review-severity-rubric` prompt partial — the label must match the prompt, so it is
  a constant, not a runtime knob (FR-014's "configurable bar" is intentionally deferred;
  the fixed-strict default is the reasonable choice).
- **Completeness feeds consistency.** The freeze only withholds what the first pass
  should have caught, so the default prompt is pushed to be exhaustive in one pass (US5),
  and an opt-in comprehensive multi-perspective mode (US3, default off) is available for
  high-stakes PRs.

## The "human decides" posture and its tradeoff (US7)

Per Constitution Principle I ("the agent proposes, the human decides"), a human PR
comment can dispose of a finding: **dismiss** ("not a problem" → not re-raised as a
blocker) or **defer** ("fix later" → non-blocking `[Deferred]`). The chosen posture is
deliberately open: **any non-bot commenter**, and **all severities including a
human-dismissed critical**.

This widens a social-engineering / prompt-injection surface — anyone commenting on a PR
can downgrade or suppress a finding. It is mitigated, not eliminated, by two controls:

- **Mandatory attribution (FR-036).** Every comment-driven suppression/downgrade names
  the commenter and quotes their rationale — never silent, so it is auditable.
- **Injection guardrail (FR-038).** A comment disposes of a *specific finding* only; it
  cannot rewrite the severity rubric or verdict rules. Instruction-like comment content
  beyond disposing of a finding is treated as untrusted (see
  `docs/security/prompt-guard.md`, `docs/security/threat-model-agent-disalignment.md`).
- **Kill-switch.** `review_dispositions.enabled: false` turns the whole behavior off for
  a security-conscious operator.

Attribution and dismiss-honoring are enforced by the prompt (the model's narrative
cannot be deterministically verified); the `[Deferred]` downgrade *is* enforced in Python
(`review_triage.enforce_deferred`). Stickiness and retraction come for free: PR comments
are the persistent store, re-read each review, so "latest comment wins".

## Related

- Contract: `specs/skills/review.md`
- Spec/plan/tasks: `specs/010-review-consistency-triage/`
- Eval harness: `docs/operations/skill-evals.md`
