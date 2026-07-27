# Quickstart — Validating Review Consistency, Triage & Discovery

How to prove the feature works end-to-end. Two layers: the **eval harness** (deterministic, CI +
live) and **manual re-review** on a real PR. See `data-model.md` and `contracts/` for details;
this is a run/validation guide, not implementation.

## Prerequisites

- `make setup` done; `KOAN_ROOT` set for any test/eval invocation.
- Provider configured (Claude CLI) for live checks; offline scorer needs no provider.

## 1. Offline evals (CI `fast` group — no provider call)

Extends the existing `koan/skills/core/review/evals/` dataset. New golden cases:

| Case | Proves | Spec |
|---|---|---|
| `repeat_stability` | same fixture reviewed twice → blocking set (🔴+🟡) identity-overlap = 100% | SC-001/002 |
| `pre_existing_downgrade` | non-critical base-code issue → 🟢 `[Pre-Existing Issue]`, `lgtm` unaffected | SC-013 |
| `disposition_dismiss` | comment "not a problem" → finding not re-raised as blocking, attributed | SC-014/016 |
| `recall_all` | N seeded issues → single default pass surfaces all within coverage | SC-012 |

Run:

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/ -k "review" -q
KOAN_ROOT=/tmp/test-koan .venv/bin/python -m app.skill_evals review        # offline scorer
```

Expected: all review unit tests green; offline scorer meets the updated `baseline.json`; a
regression (drift returns, yellow bar loosens, recall drops) exits non-zero (SC-007).

## 2. Live eval (before/after a prompt change)

```bash
KOAN_ROOT=/tmp/test-koan KOAN_EVAL_LIVE=1 .venv/bin/python -m app.skill_evals review --live
```

Compare against `evals/baseline.json`; run before and after the `review.md` / partial edits to
confirm improvement, not regression.

## 3. Manual: consistency + freeze (US1) on a real PR

1. `/review <pr-url>` → note findings {F1…}. Sidecar written to
   `instance/.review-findings/{owner}_{repo}_{pr}.json` (now with `base_sha`, `request_signature`,
   `identity_key`s).
2. `/review <pr-url>` again, **no push** → posted review reproduces the prior one (marked as a
   reproduction, not a silent no-op). *Verifies FR-001/FR-006, SC-001.*
3. Fix F1, push (touching only that area), `/review <pr-url>` → F1 suppressed as resolved; F2… recur
   unchanged; **no new non-critical finding appears on code unchanged since the prior review**; a
   `critical` pre-existing issue, if any, appears with `[Pre-Existing Issue]`. *Verifies FR-003,
   SC-003/011 — the exact whiplash the user reported.*
4. Advance the base branch (merge something into base) without touching the PR head, `/review` →
   reuse does NOT fire (re-derive + reconcile). *Verifies FR-001 base-key, D2.*

## 4. Manual: yellow-tier bar + pre-existing (US2/US6)

- Open a PR mixing a clear blocker, a borderline "should-fix", a cosmetic nit, and a non-critical
  issue in untouched surrounding code. `/review` → blocker stays 🟡/🔴; borderline → 🟢; cosmetic
  dropped; the untouched-code issue → 🟢 `[Pre-Existing Issue]`, non-blocking. Verdict merge-ready
  if no clear blocker remains. *Verifies US2, US6, SC-004/013.*

## 5. Manual: human dispositions (US7)

- On a reviewed PR, comment "this is not a problem, ignore it" on a finding; `/review` again →
  that finding is **not re-raised as blocking**, and the review states it registered the input,
  attributing the commenter + quoting the rationale. *Verifies FR-031/033/036, SC-014/016.*
- Comment "we'll fix this later" on another → it appears only as a non-blocking `[Deferred]`
  recommendation. *Verifies FR-034, SC-015.*
- Later comment "actually this is a problem" → disposition lifted, finding re-evaluated. *FR-037.*

## 6. Manual: comprehensive discovery (US3, opt-in)

- With `review_discovery.enabled=false` (default): confirm a review is identical to today
  (prompt/findings/cost). *Verifies SC-008.*
- Set `review_discovery.enabled: true` (instance or per-project): `/review` on a multi-dimension PR
  surfaces ≥ as many issues (typically more), no duplicate for the same underlying issue, and an
  immediate re-review adds ~nothing. *Verifies SC-009/010.*

## Pass criteria (roll-up)

- Offline scorer meets/exceeds baseline; live eval shows no regression (SC-006/007).
- Re-review reproduces / freezes as in §3 (SC-001/002/003/011).
- Yellow bar + `[Pre-Existing Issue]` behave as in §4 (SC-004/013).
- Dispositions honored + attributed as in §5 (SC-014/015/016).
- Discovery off = zero regression; on = recall gain + dedup (SC-008/009/010).
- `specs/skills/review.md` updated contract-first; PR declares the architectural change.
