# Research — Review Skill Evaluation Harness

**Branch**: `002-review-skill-evals` | **Date**: 2026-07-01

Phase 0 findings that anchor the plan. Each claim is verified against the codebase
(file:line cited), not assumed.

## 1. The review skill is an LLM-over-a-JSON-contract

The `/review` skill's value is: given a PR diff, produce a **structured JSON
review**. The contract is two jsonschema-style dicts in
`koan/app/review_schema.py` (`FILE_COMMENTS_SCHEMA`, `REVIEW_SUMMARY_SCHEMA`)
plus the prompt in `koan/skills/core/review/prompts/review.md`. The core
quality dimensions of a review are therefore mechanically checkable:

1. **Validity** — is the output parseable JSON that conforms to the schema?
2. **Recall** — does it flag the seeded bug in a buggy diff?
3. **Precision** — does it stay quiet (LGTM) on clean code?
4. **Severity calibration** — does it rate a real bug `critical`/`warning`, not
   `suggestion`?

This is exactly the shape a golden-case eval can score.

## 2. Reusable seams — compose, do not fork

The production pipeline already exposes clean functions the harness can compose:

- `build_review_prompt(context, skill_dir=None, ...) -> str`
  (`review_runner.py:617`) — renders the review prompt from a PR-context dict.
  The eval builds a minimal `context` (`title`, `body`, `diff`, empty comments)
  and reuses this verbatim, so the eval exercises the *same* prompt the skill
  ships.
- `_run_claude_review(prompt, project_path, timeout=600, model=None,
  project_name="") -> (output, error)` (`review_runner.py:773`) — the single LLM
  call seam. Returns `(text, "")` on success. This is the boundary the live eval
  crosses and the boundary offline tests mock.
- `_parse_review_json(raw_output) -> Optional[dict]` (`review_runner.py:1425`) —
  extracts JSON even when fence-wrapped/prose-surrounded, normalizes, and
  validates. Returns the review dict or `None`.
- `validate_review(data) -> (is_valid, errors)` (`review_schema.py:283`) — returns
  a `(bool, list[str])` tuple; does **not** raise. The eval reuses this as the
  single source of truth for "is this a valid review?".

No new schema logic is needed; FR-002 holds.

## 3. There is no existing eval infrastructure

`pr_review_learning.py` (47 KB) learns operator *preferences* from human review
comments to align the agent — it is orthogonal to a golden-case eval. Confirmed:
no golden diff fixtures, no expected-findings dataset, no precision/recall scorer,
no eval harness exist. This feature builds the harness from scratch
(Constitution VII — but there is no prior mechanism to *extend* here; the
extensibility that matters is *across skills*, addressed by the registry seam).

## 4. Test/CI constraints (load-bearing)

- **No Claude subprocess in tests** (`koan/CLAUDE.md`, constitution): the live
  eval MUST be opt-in (`KOAN_EVAL_LIVE`) and skip-without-env. Default operation
  is fully offline.
- **pytest markers** (`pyproject.toml`): only `slow` is registered. Tests run
  under `koan/` with `KOAN_ROOT` set and `PYTHONPATH=.` (`pythonpath=["koan"]`,
  imports are `from app...`). Fast group: `-m "not slow"`; slow split into 3.
  Default per-test `timeout = 60`.
- **Coverage gate** (`coverage-baseline.txt` = **88%**, 0.5% tolerance,
  `source=["app"]`): new harness code under `app/` needs strong unit coverage or
  the PR fails CI. Target ≥ 90% on the new module.
- **ruff** (`pyproject.toml`): enforced `PERF`, `SIM105`, `F541` project-wide;
  tests exempt only from `PERF`. No `# noqa` without a documented reason. Python
  3.11+ syntax only (no 3.12 `type` statements, no 3.13 `TypeVar` defaults).
- **Prompts are files, not strings**: the eval's *prompts* are the existing
  `review.md`; the eval's *data* (cases) are JSON fixtures, not prompts, so they
  live under the skill dir, not `system-prompts/`.

## 5. Data model for a golden case

A case is one JSON file:

```json
{
  "id": "sql_injection",
  "name": "F-string SQL query from user input",
  "skill": "review",
  "description": "Classic injection: user input interpolated into a SQL string.",
  "diff": "--- a/db.py\n+++ b/db.py\n@@ ...\n+    q = f\"SELECT * FROM users WHERE name='{name}'\"\n",
  "expect": {
    "expect_lgtm": false,
    "min_findings": 1,
    "require_valid_json": true,
    "expect_findings": [
      {"file": "db.py", "severity_in": ["critical", "warning"],
       "keywords": ["inject", "sql", "parameteriz", "sanit"]}
    ],
    "forbidden_files": []
  }
}
```

`FindingExpect.keywords` uses stem forms (`inject`, `parameteriz`) so wording
variation across prompt iterations doesn't make the eval brittle — a deliberate
choice to measure *behavior*, not exact phrasing (constitution: test behavior).

## 6. Scoring algorithm (deterministic, pure)

For a `(case, review_dict)`:

- `valid_json`: `validate_review(review)` → is_valid. On invalid, populate
  `schema_errors` and short-circuit remaining content checks (a structurally
  invalid review can't be meaningfully scored for recall).
- For each `expect_findings[i]`: **matched** iff some `file_comments` entry has
  `file == f.file` (exact, since diffs pin paths) AND its lowercased `comment`
  contains any keyword stem AND (if `severity_in` set) its `severity` is in the
  list. `recall = matched / len(expect_findings)`.
- `lgtm_correct`: `None` unless the case sets `expect_lgtm`; then
  `review["review_summary"]["lgtm"] == expect_lgtm`.
- `precision_penalty`: +1 per `file_comments` entry whose `file` is in
  `forbidden_files` (clean code flagged as buggy). Also flips when a clean case
  (`expect_lgtm=True`) gets a non-empty `file_comments`.
- `score`: a 0–1 blend — `0.4*valid + 0.4*recall + 0.2*(1 if lgtm_correct else 0)
  − 0.25*precision_penalty`, clamped to [0,1]. The blend weights validity and
  recall highest because those are the regressions that matter most.
- `passed`: `valid_json AND recall==1.0 AND (lgtm_correct in {None,True}) AND
  precision_penalty==0` — strict for the offline golden set; the live run reports
  the continuous score so "improvements" show even when strict pass is binary.

## 7. Live mode is a thin adapter

`review_live_fn(case, project_path)`:
`build_review_prompt(minimal_context) → _run_claude_review(...) → _parse_review_json(...)`.
It composes existing functions; the only new code is building the minimal context
dict from the case. Errors from the provider are caught → case recorded as
`errored`, run continues (FR-007).

## 8. Extensibility seam (US3)

A module-level dict `SCORERS = {"review": score_review}` maps skill → scorer.
`run_eval(cases, review_fn)` looks up the scorer per case's `skill`. Adding a
skill = `register_scorer("fix", score_fix)` + a `cases/` dir. No edit to
`run_eval`.

## Decision: what we are NOT doing (YAGNI, recorded)

- No LLM-as-judge scorer (adds nondeterminism + cost + a second model dependency;
  keyword/recall scoring is deterministic and CI-friendly).
- No automatic baseline updates or nightly cron wiring in this feature — the CLI
  writes/compares a checked-in `baseline.json`; scheduling is a later ops task.
- No second skill shipped (structure only). `fix`/`implement` evals are follow-ups.
- No review of `comment_replies`/`plan_alignment`/`close_pr` quality in v1 — the
  golden set scores `file_comments` + `review_summary`, the parts that carry the
  findings.
