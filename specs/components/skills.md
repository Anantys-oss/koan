---
type: component-spec
title: "Component Spec — Skills System"
description: "Documents the skills system that discovers, routes, and executes `/command` skills (SKILL.md contract, dispatch, the new-skill checklist, and the eval harness)."
tags: [skills]
created: 2026-06-27
updated: 2026-07-22
---

# Component Spec — Skills System

**Modules:** `koan/app/skills.py`, `koan/app/skill_dispatch.py`,
`koan/app/external_skill_dispatch.py`, `koan/skills/core/<name>/`,
`instance/skills/<scope>/<name>/`

> Per-skill specs live in `specs/skills/`. This spec covers the **system** that
> discovers, routes, and executes skills.

## Purpose

An extensible command-plugin system. A "skill" is a `/command` with a `SKILL.md`
(frontmatter contract) and an optional `handler.py`. Skills are how both humans (Telegram,
dashboard) and external systems (GitHub/Jira @mentions) drive Kōan.

## Architecture

```
skills.py            → registry: discover SKILL.md, parse frontmatter (lite YAML),
       │                map commands/aliases → skills, execute_skill()
skill_dispatch.py    → agent-loop direct execution: /command missions bypass the Claude
       │                agent, route to registered runners (plan/rebase/recreate/check/...)
external_skill_dispatch.py → in-process dispatch for custom skills triggered via Jira/GitHub
```

## Skill anatomy

```
koan/skills/core/<name>/
  ├─ SKILL.md      # frontmatter: name, description, group, commands, aliases, flags
  └─ handler.py    # optional: def handle(ctx: SkillContext) -> Optional[str]
```

- **Handler return contract:** string → Telegram reply; `""` → already handled; `None`
  → no message.
- **Prompt-only skills:** omit `handler`, put prompt text after frontmatter → sent to
  Claude directly.

## Frontmatter flags (the contract)

| Flag | Meaning |
|---|---|
| `group:` | **Mandatory.** One of: missions, code, pr, status, config, ideas, system (core); `integrations` reserved for custom skills. Drives `/help`. |
| `worker: true` | Blocking skill (Claude/API) → runs in a background thread. |
| `github_enabled: true` | Triggerable via GitHub @mention (Jira reuses it; no separate `jira_enabled`). |
| `github_context_aware: true` | Accepts extra context after the command. |
| `sub_commands:` | Combo skill — decomposes into multiple sub-missions (discovered by `collect_combo_skills()`). |
| `forward_result: true` (+ `title_markers:`) | Opt-in result forwarding, resolved dynamically — **the pattern for "core recognizes a custom skill" without hardcoding names**. |
| `model_key:` | Selects the model tier (e.g. `mission`). |

## Invariants

- **Names/aliases/dirs use underscores, never hyphens** — Telegram truncates at `-`.
- **No hardcoded skill-name lists in `koan/app/`.** When core must recognize a specific
  custom skill, drive it off SKILL.md frontmatter flags (see `collect_forward_result_markers`).
- **Skill stdout is DATA.** Runners emit structured transcripts; `mission_executor`
  passes `trust_stdout=False` so transcripts aren't misread as CLI errors.
- **No private identifiers leak** into core skills, tests, or docs — use generic
  placeholders (`my_fix`, `my_team`, `PROJ-NNN`).
- **Order-sensitive combos insert atomically** — one locked multi-entry write
  (`insert_pending_missions`), never two top-inserts (TOCTOU + reversed order).
- **Per-skill project instructions are mechanism, not enumeration.** `.koan/skills/<name>/*.md`
  is injected by `prompts._maybe_append_project_skill_instructions`, gated on the skill dir
  having a `SKILL.md` and the caller passing `project_path` — never a hardcoded skill list.
  New skills get it for free.

## Adding a core skill (full checklist)

1. `koan/skills/core/<name>/SKILL.md` (+ `handler.py` if needed) with a `group:`.
2. If agent-loop-run: register in `_SKILL_RUNNERS` + `_COMMAND_BUILDERS` +
   `validate_skill_args()` in `skill_dispatch.py`.
3. Add to the CLAUDE.md "Core skills" list (alphabetical).
4. Update `docs/users/user-manual.md` and `docs/users/skills.md`.
5. Add the per-skill spec in `specs/skills/<name>.md`.
6. `TestCoreSkillGroupEnforcement` must pass (fails if `group:` is missing).
7. If the skill is LLM-driven and has a checkable output contract, add eval cases
   (see "Skill evaluation harness" below). Orchestration/queue skills with no
   structured LLM output are exempt — see `EVAL_EXEMPT_SKILLS`.

## Skill evaluation harness

**Module:** `koan/app/skill_evals.py` — a deterministic framework for evaluating
LLM-driven skills against a checked-in golden dataset, so quality regressions
are caught in CI and improvements are measurable across prompt iterations.

**Rule (constitution VII — honest reporting):** a skill gets golden-dataset
evals **iff** it is LLM-driven **and** emits a checkable structured output
(valid JSON / a parseable contract). Skills that lack such a contract are
documented as exempt — fabricating a dataset for them would measure nothing
real. This is enforced at contribute time by the new-skill checklist (step 7)
and pinned by the `TestEvalExemption` guard.

**Covered skills** (scorer + `evals/cases/` + live adapter, all keyed by name in
the `SCORERS` / `LIVE_FNS` registries — adding a skill never edits `run_eval`):

| Skill | Output contract | Scorer reuses |
|---|---|---|
| `review` | JSON findings (`review_schema`) | `validate_review` |
| `fix` | diagnostic `{confidence, hypothesis, code_paths}` | `_parse_diagnostic` shape |
| `plan` | markdown (sections + `#### Phase N:`) | `parse_plan_progress` |
| `brainstorm` | JSON `{issues[]}` w/ 7 `REQUIRED_ISSUE_SECTIONS` | `_parse_decomposition` + `_validate_issue_bodies` |
| `rebase` | JSON `{already_solved, confidence}` decision | `_check_if_already_solved` rule |

**Exempt skills** (`EVAL_EXEMPT_SKILLS`, pinned by a guard test — quality bar is
behavioural unit tests instead):

| Skill | Why exempt |
|---|---|
| `implement` | orchestration: `run_implement()` returns `(success, summary)`, mutates files + opens a PR — no structured artifact to score |
| `mission` | pure-Python queue utility — no LLM at all |

- **Per-skill data** lives with the skill: `koan/skills/core/<name>/evals/cases/*.json`
  (golden inputs + expectations) + `evals/baseline.json` (last-known-good live
  scores). `EvalCase.diff` is the `review` input; other skills carry inputs in
  `EvalCase.input` (e.g. `issue_*`, `idea`, `topic`, `pr_*`).
- **Scorer dispatch** is keyed by skill name via the `SCORERS` registry
  (`register_scorer`/`get_scorer`); the CLI resolves the live adapter per skill
  via `LIVE_FNS` (`get_live_fn`), reporting "no live adapter" honestly when absent.
- **Two modes:** offline (default, CI-safe — scores canned outputs, never calls
  the Claude subprocess) and live (opt-in via `KOAN_EVAL_LIVE`, composes the
  skill's real pipeline seams, compares to `baseline.json`, exits non-zero on
  regression).
- **Single source of truth:** each scorer reuses that skill's own existing
  validator/parser rather than re-implementing the contract.

**Design contract:** `specs/002-review-skill-evals/` (review),
`specs/003-core-skill-evals/` (multi-skill). **Operator runbook:**
`docs/operations/skill-evals.md`.

## Integration points

- Bridge dispatch (`command_handlers.py`) and agent-loop dispatch (`mission_executor`)
  both call into `skills.py`.
- Custom skills under `instance/skills/<scope>/` can be cloned Git repos for team sharing
  (`skill_manager.py`).
- GitHub/Jira @mentions route through `external_skill_dispatch.py`.

### MCP access for skill runners

Skill runners invoked via `run_command()`/`run_command_streaming()` receive MCP
servers only when their role is listed in `config.mcp_roles` (see
`specs/components/providers.md` → "MCP per-role boundary"). Runners consuming
untrusted input (GitHub/Telegram text) stay excluded by default. To opt a new
runner in, pass `config.mcp_configs_for_role("<role>", project_name)` — never
`get_mcp_configs()` directly — so the gate and kill switch always apply.

### `review` diff-size & partial-coverage contract

- **Single source of truth for diff size** is the compressor token budget
  (`optimizations.review_compressor.token_budget`, default 80,000), read via
  `config.get_review_compressor_token_budget()`. The fetch-time character cap is
  *derived* from it — `config.get_review_max_diff_chars()` = budget × 3.5 × 4 —
  so there is no independent, conflicting numeric cap. When the compressor is
  *disabled* (`review_compressor.enabled: false`), no packer re-shrinks the diff,
  so `build_review_prompt()` applies a token-safe backstop
  `config.get_review_uncompressed_max_diff_chars()` = budget × 3.5 (no headroom)
  via `utils.truncate_diff_with_skips()` — the size guard holds in every config
  and its skips feed the same coverage note.
- `fetch_pr_context(...)` (in `rebase_pr.py`) takes `max_diff_chars` (legacy default
  32 000 for rebase/squash/recreate/ci_queue callers; `/review` passes the derived
  cap) and returns a `diff_skipped_files` list via `utils.truncate_diff_with_skips()`
  so files cut at fetch time are first-class, not buried in the diff footer.
- `review_runner.build_review_prompt()` returns a `(prompt, coverage_note)` tuple.
  `_build_coverage_note()` merges fetch-time skips, compressor skips, and triaged
  files into **one** value used both for the `{SKIPPED_FILES}` prompt slot and the
  returned note — the two can never diverge. `_post_review_comment(..., coverage_note=)`
  prepends the note (a `⚠️ Partial review` block) above the review body, before the
  60 K GitHub-length truncation, so partial coverage is never silent.
- **Repo-owner pin set (`.koan/config.yaml` → `review.always_check`).** A target repo
  may pin files so diff-size reduction never silently drops them. `build_review_prompt`
  reads `project_koan.get_review_always_check(project_path)` (a list of file globs) and
  threads it as `pinned_patterns=` into **both** skip paths — `diff_compressor.compress_diff`
  (on-path) and `utils.truncate_diff_with_skips` (compressor-off / char backstop). A
  changed file whose repo-relative path **or** basename matches any pattern
  (`fnmatch.fnmatchcase`, case-sensitive; via `diff_compressor.path_matches_any`) sorts
  ahead of non-pinned files, so it consumes budget first and is never *fully* skipped
  while budget remains. Pinning reorders inclusion **only** — it never raises the token/
  char budget (the budget stays the single source of truth for size), so an enormous
  pinned file still degrades to partial hunks like any oversized file. The `pinned_patterns`
  parameter is **optional with a no-pin default**, so every non-review caller
  (rebase/squash/recreate/ci_queue) and an absent/empty/malformed config are byte-identical.
  Because a pinned-and-included file never enters the compressor/backstop skip list, it is
  **absent from the `⚠️ Partial review` coverage note** — the note keeps listing only
  genuinely-omitted files, preserving the one-value "prompt and posted body never diverge"
  invariant above. When ≥1 file is actually pinned, `build_review_prompt` logs one
  `review` line; no pins ⇒ no line. The review output **schema and prompt templates are
  unchanged**, so the eval golden dataset/baseline are unaffected.

### Project-local skill instructions (.koan/skills/<name>/)

A target project may ship `<project>/.koan/skills/<skill>/*.md` to append authoritative
extra instructions on top of a core skill's built-in prompt (append-only; no override in
this phase). `project_koan.read_skill_instructions()` reads the `*.md` files sorted by
filename (each behind a `# <filename>` provenance marker), ignores non-`.md` files and
subdirs, and caps the concatenation at `_MAX_KOAN_SKILL_CHARS` (16k).
`prompts._maybe_append_project_skill_instructions(prompt, skill_dir, project_path)` frames
the content via the `koan-skill` template and appends it — **only** when `skill_dir` has a
`SKILL.md` and `project_path` is set (default `None` ⇒ byte-identical no-op). Scope:
runner/loader-based skills that thread `project_path` (`review`, `refactor`, `plan`, …)
honor both `.koan/skills/<skill>/*` and general `KOAN.md` (see "General KOAN.md in skill
prompts" below); prompt-only skills (`_execute_prompt` returns raw `prompt_body`, no
loader) receive neither, as they run without a resolved project in scope. Precedence:
mission instruction > `.koan/skills/<skill>/*` > `KOAN.md`/`.koan/KOAN.md` > skill built-in
prompt > `CLAUDE.md`/defaults.

### General KOAN.md in skill prompts

`prompts.load_skill_prompt` also injects the project's **general** `KOAN.md`
(root `KOAN.md` + `.koan/KOAN.md`, read via `project_koan.read_general_koan_md`,
combined cap `_MAX_KOAN_MD_CHARS` 16k) for every runner/loader-based skill that
threads `project_path`. `prompts._maybe_append_general_koan_md(prompt, skill_dir,
project_path)` frames it via the shared `koan-md` template (the same framing the
agent loop uses in `prompt_builder._get_koan_md_section`) and appends it **after**
the `.koan/skills/<skill>/*` block, so a single skill prompt carries both — ordered
`.koan/skills/<skill>/* > KOAN.md`. Gated identically to the per-skill append: a
no-op unless `skill_dir` has a `SKILL.md` **and** `project_path` is set (default
`project_path=None` ⇒ byte-identical). This makes the precedence chain above real
for skills, not only for the agent loop. The two blocks keep independent 16k caps
(no combined ceiling — worst case 32k on one prompt, only when a project ships both
a large root `KOAN.md` and large per-skill instructions). Prompt-only skills
(`_execute_prompt` returns raw `prompt_body`) run without a resolved project in
scope, so they receive no project-scoped injection — by design, not a gap.

Every actual injection is announced on **stderr** (so it lands in `logs/run.log`
and is visible via `make logs`) through `project_koan.log_context_load(label,
content)`, which emits `Detected <label>, loaded N chars (~ M tokens)` — `label`
is `KOAN.md` for the general block and `.koan/skills/<skill>` for the per-skill
block. `logging.getLogger` output alone is invisible in the run loop (no
stream handler wired), so the load line is a direct `print`, not `logger.info`.

### Repo config file (`.koan/config.yaml`)

`project_koan.read_koan_config(project_path)` reads an optional structured
`<project>/.koan/config.yaml` — a **second, YAML** surface alongside the markdown
`.koan/KOAN.md` / `.koan/skills/` steering files, scoped to the *target repo* (distinct
from the operator's KOAN_ROOT `instance/config.yaml`). It is a generic, extensible
per-repo config designed to gain keys over time; this phase ships exactly one key,
`review.always_check` (see the diff-size contract above), consumed via the typed accessor
`get_review_always_check(project_path) -> list[str]`.

**Fail-safe contract (untrusted-input hardening).** Every malformed shape converges to the
absent-config no-op — the reader NEVER raises and NEVER aborts a review:

- `read_koan_config` does `yaml.safe_load` (never `load`) and returns `{}` on a missing
  file, unparseable YAML, unreadable file, or a non-mapping top-level value (at most one
  diagnostic logged). Unknown top-level and unknown `review.*` keys are ignored
  (forward-compatible).
- `get_review_always_check` returns `[]` unless `review.always_check` is a list; it keeps
  only non-blank `str` items, and caps at `_MAX_ALWAYS_CHECK_PATTERNS` (100) patterns of
  `_MAX_PATTERN_LEN` (200) chars each, dropping the excess with one diagnostic.

Absent `.koan/config.yaml` (the common case) ⇒ `[]` ⇒ byte-identical review output.

### `add_project` workspace resolution (contract)

The clone target and project discovery MUST resolve the workspace directory
through the single helper `app.workspace_discovery.resolve_workspace_dir`
(prefers `<root>/instance/workspace` when present, else `<root>/workspace`).
Any new writer of workspace projects resolves through this helper — a writer
that hardcodes `<root>/workspace` will place clones where discovery does not
scan on hosted deploys (the #2338 regression, where `/add_project` cloned into
`<root>/workspace` while discovery read `<root>/instance/workspace`). The
`add_project` handler, `discover_workspace_projects`, and the merged-registry
cache-invalidation mtime (`projects_merged._get_workspace_mtime`) all share
this one resolver so write-path, read-path, and cache-watch stay aligned.

### `/claudemd` learnings mode (contract)

`/claudemd <project>` has two behaviors, routed by an optional sub-argument:

- **default (git-history refresh):** `/claudemd <project>` updates or creates
  `CLAUDE.md` from architecturally significant commits (`run_refresh`).
- **`learnings` mode:** `/claudemd <project> learnings` distills Kōan's
  per-project learnings (`instance/memory/projects/<name>/learnings.md`) into
  a **delimited managed block** in the project's own `CLAUDE.md`
  (`run_learnings_sync`).

The `learnings` sub-argument is detected case-insensitively in both the handler
(which appends it to the queued mission) and `skill_dispatch._build_claudemd_cmd`
(which appends `--mode learnings`); the default path MUST remain flag-free.

The managed block is delimited by the marker constants
`KOAN_LEARNINGS_BEGIN` / `KOAN_LEARNINGS_END`. Invariants the mode MUST uphold:

- **Managed-block boundary.** Only the delimited region mutates;
  `upsert_koan_learnings_block` preserves every non-block byte verbatim and is
  idempotent (identical distilled input → byte-identical output, exactly one
  block, no accumulation). The replacement is regex-metacharacter-safe (lambda
  replacement, never a string that would interpret `\1`/`\g<0>`).
- **Dedup against current CLAUDE.md.** The distiller is handed the current
  `CLAUDE.md` and instructed to skip conventions already documented, so the
  block never duplicates human-authored content.
- **Durable-only filter.** Only conventions that hold regardless of any
  specific bug are kept; transient/bug-specific quirks are dropped.
- **Draft-PR delivery.** A real diff is delivered as a draft PR on a
  `<prefix>sync-learnings-*` branch — no branch is created for the no-op paths
  (missing/empty learnings, `NO_DURABLE_LEARNINGS` sentinel or empty
  distillation, or an unchanged block), which return 0 and write nothing.
- **Injection containment.** Distillation runs stdout-only (`allowed_tools=[]`,
  `max_turns=1`); no file/network side effects, so a poisoned `learnings.md`
  can at worst yield reviewable text in a draft PR.

## Known debt / watch-outs

- Frontmatter is parsed by a custom lite YAML parser (no PyYAML) — keep frontmatter
  simple; exotic YAML will not parse.
- ~80 of ~91 skills lack per-skill specs (phase 1 ships 10 exemplars).

## Change protocol

Changing the SKILL.md contract, dispatch routing, or the group enumeration updates this
spec, the README authoring guide (`koan/skills/README.md`), and the group-enforcement
test.
