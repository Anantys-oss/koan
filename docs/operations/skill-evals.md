# Skill evaluation (eval) harness

Kōan ships a small, deterministic **eval harness** that scores an LLM-driven skill
against a checked-in golden dataset. It exists to **catch quality regressions in
CI** and to **measure improvements** across prompt iterations. It covers the
skills that are LLM-driven **and** emit a checkable structured output:
`review`, `fix`, `plan`, `brainstorm`, `rebase`. Skills without such a contract
(`implement`, `mission`) are exempt — see [Why some skills are exempt](#why-some-skills-are-exempt).

**Code:** `koan/app/skill_evals.py` · **Design:** `specs/002-review-skill-evals/`
(review), `specs/003-core-skill-evals/` (multi-skill) · **Cases:** `koan/skills/core/<skill>/evals/`

## Covered skills

The harness scores any skill that is LLM-driven **and** emits a checkable
structured output. Each has its own scorer (reusing the skill's own validator as
the single source of truth), a `cases/` dataset, and a live adapter:

| Skill | Output contract | What the scorer checks | Live command |
|---|---|---|---|
| `review` | JSON findings | validity, recall, LGTM, precision | `python -m app.skill_evals review --live` |
| `fix` | diagnostic `{confidence, hypothesis, code_paths}` | confidence validity/match, hypothesis + code-path recall | `… fix --live` |
| `plan` | markdown (sections + phases) | required sections, min phases, no placeholders, title | `… plan --live` |
| `brainstorm` | JSON `{issues[]}` | JSON validity, count range, per-issue sections, priority, score bars, themes | `… brainstorm --live` |
| `rebase` | JSON `{already_solved, confidence}` | decision correctness (`already_solved && high`), confidence, reasoning | `… rebase --live` |

A case carries its input in `diff` (for `review`) or in a generic `input` block
(`issue_*` for `fix`, `idea` for `plan`, `topic` for `brainstorm`, `pr_*` for
`rebase`). See each skill's `evals/cases/` for the exact shape.



## Why two modes

The review skill's output is a structured JSON review. Whether a review is *good*
boils down to a few mechanically-checkable things: is the JSON valid? does it
catch a seeded bug? does it stay quiet on clean code? does it rate severity
sanely? The harness scores exactly those.

- **Offline (default, CI-safe).** Scores canned outputs and validates the
  dataset. It **never calls the Claude subprocess**, so it runs in the normal
  `fast` test group on every PR. This is what stops a broken harness or a
  malformed case from landing.
- **Live (opt-in).** Actually invokes the review pipeline over the dataset and
  compares the result to a checked-in `baseline.json`. This is the tool you run
  **before and after** changing the review prompt, to confirm an improvement or
  catch a regression.

## Run the offline checks (CI)

```bash
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_skill_evals.py -v
```

These tests score the harness itself (scorer correctness, dataset validity,
baseline comparison, CLI) using inline fixtures — no model required.

## Run the live eval

The live eval calls the real review CLI, so it needs a configured review provider
(see the [provider docs](../providers/)) and must be opted into explicitly:

```bash
KOAN_EVAL_LIVE=1 python -m app.skill_evals review --live
```

Output is a per-case + aggregate report:

```
# Eval report — skill: review
cases: 5  passed: 4  errored: 0

## Aggregate metrics
  valid_json_rate: 1.0
  mean_recall: 0.67
  lgtm_accuracy: 0.8
  mean_score: 0.82
  pass_rate: 0.8

## Baseline: regressed
  valid_json_rate: unchanged
  mean_recall: regressed
  ...
```

The process **exits non-zero on any regression** (FR-009), so it can gate a
prompt change in a script or CI job you control. It refuses to run without
`KOAN_EVAL_LIVE` set.

### Establishing / updating the baseline

The first time, or after a deliberate prompt improvement you want to lock in:

```bash
KOAN_EVAL_LIVE=1 python -m app.skill_evals review --live --update-baseline
```

This writes the current run's metrics to
`koan/skills/core/review/evals/baseline.json`. Commit that file — the next run
compares against it. Drop the flag again to compare-only.

## The golden dataset

Each case is one JSON file under `koan/skills/core/review/evals/cases/`:

| Case | Type | Expectation |
|---|---|---|
| `sql_injection` | seeded bug | flag `db.py` at `critical`/`warning` |
| `bare_except` | seeded bug | flag `worker.py` (`warning`+) |
| `hardcoded_secret` | seeded bug | flag `config.py` at `critical` |
| `clean_refactor` | precision | LGTM, no flag on `utils.py` |
| `benign_style` | precision (false-positive trap) | LGTM, no flag on `report.py` |

### Add a case

Drop a new `<id>.json` into `cases/` — no code change needed. A case has:

```json
{
  "id": "my_case",
  "name": "Short human title",
  "skill": "review",
  "description": "What the diff does and why it matters.",
  "diff": "--- a/x.py\n+++ b/x.py\n@@ ...\n+<the change>\n",
  "expect": {
    "expect_lgtm": false,
    "min_findings": 1,
    "require_valid_json": true,
    "expect_findings": [
      { "file": "x.py", "severity_in": ["critical", "warning"],
        "keywords": ["inject", "sql", "parameteriz"] }
    ],
    "forbidden_files": []
  }
}
```

- `keywords` are **stem forms** (e.g. `inject`, `parameteriz`) — a finding matches
  if its comment contains any of them. Stems keep the eval robust to wording
  changes across prompt iterations; we measure *behaviour*, not exact phrasing.
- `severity_in` optionally constrains the severity band; omit it to accept any.
- `forbidden_files` flags false positives — files that must **not** receive a
  comment (precision discipline for clean cases).

`load_cases` validates every case at load time and raises with the file + reason
if one is malformed, so a bad case fails loudly in CI.

## What the score means

`score_review` blends (per `specs/002-review-skill-evals/research.md`):

- `0.4 × validity` + `0.4 × recall` + `0.2 × lgtm-correctness` − `0.25 × precision-penalty`
  (clamped to `[0, 1]`).

Validity and recall dominate because those are the regressions that matter most.
`passed` is strict for the offline golden set (valid + full recall + correct
LGTM + no forbidden flags); the **continuous `score`** is what the live run diffs
against the baseline, so partial improvements show up even when strict pass is
binary.

## Why some skills are exempt

Two core skills are deliberately **not** covered: `implement` and `mission`
(listed in `EVAL_EXEMPT_SKILLS`, pinned by a guard test). Forcing a golden
dataset onto them would measure nothing real (constitution VII — honest
reporting):

- **`implement`** is orchestration — `run_implement()` returns `(success,
  summary)`, mutates files, and opens a PR. There is no structured artifact to
  score; its quality bar is "did the code work, did tests pass, was the PR
  created", which is CI + behavioural tests (`test_implement_runner.py`).
- **`mission`** is a pure-Python queue utility — `handler.py` calls
  `insert_pending_mission` and involves no LLM at all.

**The rule:** before adding evals to a skill, confirm it actually emits a
structured LLM output with a validator/parser seam. If it does not, document it
as exempt (add to `EVAL_EXEMPT_SKILLS` with rationale) rather than fabricating
cases.

## Extend to another skill

The harness is generic on `(case, output, scorer)`, keyed by skill name:

1. Write a scorer `score_<skill>(case, output) -> CaseResult` (reuse that skill's
   own validator as the single source of truth, the way the review scorer reuses
   `validate_review`).
2. `register_scorer("<skill>", score_<skill>)`.
3. Add `koan/skills/core/<skill>/evals/cases/*.json`.

`run_eval` dispatches per case via the `SCORERS` registry, so adding a skill never
requires editing it. See `specs/components/skills.md` → "Skill evaluation harness".

## Constraints (why it's built this way)

- **No Claude subprocess in the default test suite** (project rule). Live eval is
  strictly opt-in (`KOAN_EVAL_LIVE` + `@pytest.mark.slow`).
- **No new dependencies** — stdlib only; the live adapter composes the existing
  review seams (`build_review_prompt` → `_run_claude_review` → `_parse_review_json`)
  rather than duplicating review-pipeline logic.
- **Hermetic live runs** — memory injection is disabled when building the eval
  prompt, so scores don't depend on the operator's `learnings.md`.
