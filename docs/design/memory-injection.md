# Memory Injection into Skill Prompts

Status: shipped on commit `d9dfab2` — `feat(memory): wire memory into skill prompts`.

This document describes how Kōan's per-project memory is assembled and threaded
through both the autonomous agent loop and the five mission-driving skills
(`/fix`, `/plan`, `/implement`, `/refactor`, `/review`), plus the two growth-
control tricks that ship with it.

---

## 1. Problem this solves

Before this commit, project memory had two structural holes:

| Hole | Symptom |
|------|---------|
| **Skills bypassed memory** | The five skills built their own prompts and saw none of `learnings.md`. Only the autonomous agent loop benefited from it. `/fix issue-42` on a project that had spent months teaching the agent to never touch the migrations folder got no warning. |
| **Two template files were inert** | `memory/projects/_template/context.md` and `priorities.md` shipped in the template but were never read or written by any code path. Operators who filled them in saw no behavioral change. |

A third issue lived in the growth-control side: the periodic 24h semantic
compaction used **exact-string** dedup for incoming PR-review lessons, so any
paraphrase ("test PR changes" vs. "verify changes with tests") accumulated
between compaction cycles. And compaction itself ran unconditionally on every
schedule tick — even when the file had barely grown, burning lightweight-model
quota for negligible savings.

---

## 2. What ships

### 2.1 A single shared memory-injection helper

New module **`koan/app/skill_memory.py`** — sole source of truth for "give me a
memory block for this project, scoped to this task." It produces an XML-fenced
block:

```
<memory-context>
# Project Memory

## Context (human-curated)
<verbatim context.md, capped at 80 lines>

## Priorities (human-curated)
<verbatim priorities.md, capped at 40 lines>

## Learnings (filtered — K of N)
<Jaccard-scored learnings against the task text>
</memory-context>
```

Two public entry points:

- `build_memory_block(instance, project_name, task_text, ...)` — the canonical
  builder, used by the agent loop via `prompt_builder._get_learnings_section`.
- `build_memory_block_for_skill(project_path, task_text, **kwargs)` —
  convenience wrapper used by skill runners. Resolves `KOAN_ROOT` from the
  environment and accepts an explicit `project_name` from skill dispatch when
  available. Without one, it **reverse-resolves `project_path` against Koan's
  merged project registry**: `projects.yaml` plus dynamically discovered
  `KOAN_ROOT/workspace/<project>` directories. Operators whose configured slug
  differs from the repo directory name (e.g. `path: ~/code/koan-fork` mapped
  to `name: koan`) still get memory injected, and workspace-only projects are
  treated as first-class memory scopes.

Source rules:

| Source | Loading | Filtering | Cap |
|--------|---------|-----------|-----|
| `learnings.md` | Jaccard scoring vs. task text via `memory_recall.score_and_select` | Honors `[recall:full]` escape hatch | `memory.max_relevant_learnings` (default 25 for skills, 40 for agent loop) |
| `context.md` | Verbatim | None | 80 lines |
| `priorities.md` | Verbatim | None | 40 lines |

If every source is empty/missing, the helper returns `""` so callers can
unconditionally substitute the placeholder.

A defensive guard (`_is_safe_project_name`) rejects `..`, path separators, and
leading-dot names — today every caller is operator-controlled, but the function
is the chokepoint for any future untrusted input.

### 2.2 Skills now inject memory

Each of the five mission-driving skills calls the helper and passes the result
as a `{PROJECT_MEMORY}` placeholder into its prompt:

| Skill | Runner | Prompt(s) |
|-------|--------|-----------|
| `/fix` | `koan/skills/core/fix/fix_runner.py` | `fix.md` |
| `/implement` | `koan/skills/core/implement/implement_runner.py` | `implement.md` |
| `/plan` | `koan/app/plan_runner.py` (new + iterate paths, plus review loop) | `plan.md`, `plan-iterate.md` |
| `/review` | `koan/app/review_runner.py` | `review.md`, `review-architecture.md`, `review-with-plan.md` |
| `/refactor` | _(via the agent loop)_ | inherits the agent.md path |

For `/review`, scoring uses **title + body + first 2K chars of diff** instead
of "title + branch" — branch names like `koan/fix-issue-123` produce near-zero
Jaccard signal, while the diff is where the file paths and module names that
the learnings file actually indexes against live.

### 2.3 Agent loop now sees `context.md` and `priorities.md`

`prompt_builder._get_learnings_section()` delegates to the new helper (with
defaults of 40 / 5 instead of 25 / 3 because the agent loop has more prompt
headroom). The visible change to operators: the existing "Project Learnings
(filtered)" section is now a richer `<memory-context>` block with sub-sections
per source, plus the two template files actually do something.

The agent-loop section in `koan/system-prompts/agent.md` was rewritten to
document all three sources and tell Claude that `context.md`/`priorities.md`
are human-only territory.

### 2.4 Anti-thrash guard on semantic compaction

`memory_manager.compact_learnings()` now skips when running the compaction CLI
would barely move the needle.

Two flavours, in priority order:

1. **Growth-aware** — when prior state knows how many lines the file held
   right after the last successful compaction, skip if the file has grown by
   less than 10% relative to that baseline. This is the most accurate signal
   ("almost nothing has been added since last cycle").
2. **Target-distance fallback** — when there's no prior telemetry (first
   compaction ever, legacy plain-hash state, or non-dict JSON), skip if
   `(original - max_lines) / original < 10%`.

The state file format upgraded from a plain hex hash to JSON:

```json
{"hash": "<sha256>", "compacted_lines": 87, "updated_at": "2026-05-17T..."}
```

Legacy plain-hash files are tolerated and rewritten in JSON on the next
successful compaction. Skipped runs return `{"skipped": true, "reason":
"anti_thrash"}` in the stats dict so `run_cleanup()` can distinguish them
from "no change" skips.

### 2.5 Write-time semantic dedup for PR-review lessons

`pr_review_learning._append_lessons_to_learnings()` runs a two-pass dedup:

1. **Exact-string** against existing lines (cheap, always runs). Drops any
   candidate that already appears verbatim.
2. **Semantic** via a lightweight Claude pass (15s timeout, 1 turn,
   `max_attempts=1`) on the candidates that survive pass 1. Catches
   paraphrases — "test PR changes" vs. "verify changes with tests". A final
   exact-string sweep absorbs any echoed existing line from the CLI output.

Gated by `memory.write_time_dedup` in `config.yaml` (default `true`). Falls
back transparently to pass-1-only dedup on CLI failure or timeout. Skipped
entirely when the existing file is empty or `project_path` is unknown.

The prompt lives in `koan/system-prompts/learnings-dedup.md` and explicitly
tells the model to **preserve exact wording** and to **keep on doubt** — the
periodic semantic compaction pass will merge what slips through.

### 2.6 Themed compaction output

`koan/system-prompts/learnings-compaction.md` was updated to organize the
compacted output into themed sections:

```
## Conventions
## Gotchas
## Rejected-PR lessons
## Architecture notes
## Other      (fallback when nothing fits the four themes above)
```

Empty sections are not emitted. This is purely a presentation change — same
lines come out, grouped differently — but it makes the file dramatically
easier for a human to skim, and gives the Jaccard scorer better local
neighborhoods to draw from.

### 2.7 Config knob

```yaml
# instance.example/config.yaml
memory:
  write_time_dedup: true   # default; set to false to save quota
```

---

## 3. Benefits

- **Skills now align with project conventions.** A `/fix` on a project where
  the agent has previously learned "never edit `instance/`" sees that rule in
  its prompt. Before, only the autonomous loop saw it.
- **Two dead template files become first-class memory.** Operators who fill
  in `context.md` (architecture, stakeholders) and `priorities.md` (current
  focus, no-touch zones) finally get the behavioral payoff. Both files are
  marked human-only — the compaction pipeline ignores them, so notes you
  write stay exactly as written.
- **Single source of truth for memory assembly.** Before, the agent loop had
  its own learnings-loading code and the skills had nothing; now both paths
  go through `skill_memory.py`. One bug to fix, one set of caps to tune.
- **Quota saved on idle compaction cycles.** The anti-thrash guard skips the
  ~120s lightweight-model call when the file has barely grown. On a quiet
  day across N projects, that's N×120s/day of quota recovered.
- **Paraphrased duplicates die at the write boundary.** Write-time semantic
  dedup prevents lessons.md from drifting into "five ways to say the same
  thing" between 24h compaction cycles.
- **Cross-instance portability for project slugs.** The reverse-lookup
  against the merged project registry means forks/clones whose directory name
  doesn't match the configured project name still get memory injected, while
  repos discovered from `workspace/` work without a `projects.yaml` path entry.
- **Better Jaccard signal for `/review`.** Scoring against title + body +
  diff slice (not branch name) means the right lessons surface for the right
  PRs, not whichever lessons happen to share a word with `koan/fix-issue-N`.
- **No regression in the cache prefix.** The memory block lives in the
  *user* prompt (varies per mission) so it doesn't poison the prefix-cached
  system prompt — that split is preserved by `build_agent_prompt_parts`.

---

## 4. Limits

- **Jaccard is dumb.** Word-overlap scoring will miss semantically related
  lessons that don't share tokens with the task text, and will surface
  unrelated lessons that happen to share common words. `[recall:full]` is
  the escape hatch, but it costs prompt budget. A true embedding-based
  recall would do better — but adds a model dependency and a cache to
  manage.
- **Verbatim caps are line-based, not token-based.** An 80-line
  `context.md` can be either 400 or 4,000 tokens depending on how prose-y
  it is. A runaway operator who pastes a long Markdown table fits inside
  the cap but blows the prompt budget. A token-aware cap (using
  `tiktoken` or the provider's tokenizer) would be sharper.
- **Write-time dedup is best-effort.** 15s timeout + 1 attempt means a
  slow Anthropic API moment silently falls back to exact-string dedup. No
  warning to the operator — the only visible signal is that paraphrased
  duplicates show up in `learnings.md` until the next periodic compaction.
- **Anti-thrash is a heuristic.** 10% growth is reasonable but arbitrary;
  a project that adds 9 lines/day for weeks never triggers compaction
  growth-wise even though the file accumulates 60+ lines/week of small
  drift. The target-distance fallback catches some of this once the file
  is far enough from `max_lines`, but there's a dead zone.
- **`context.md` / `priorities.md` are read every mission.** No caching,
  no change detection — every skill invocation re-reads them. Fine today
  (small files, local disk) but could matter at scale or on NFS-backed
  KOAN_ROOTs.
- **Three sources concatenated unconditionally.** If `context.md` and
  `priorities.md` are both filled in and `K` learnings get past the
  Jaccard filter, the resulting block can run 120+ lines, all in the
  user prompt every mission. There's no overall budget — just per-source
  caps that don't compose.
- **`build_memory_block_for_skill` returns `""` when `KOAN_ROOT` is
  unset.** That's correct for standalone skill invocations outside an
  instance, but it means a Kōan instance with a broken/missing env var
  silently strips memory from every skill prompt — no warning logged.
- **Reverse-resolution failures fall back to basename silently.** If
  `projects.yaml` is malformed and the lookup raises, the helper warns
  and uses the directory basename — an operator who renamed only one
  side won't notice the memory has gone dark.
- **Themed compaction sections aren't validated.** The prompt asks the
  model to emit only the four themed sections plus `## Other`, but
  there's no parser that enforces this. A model that invents extra
  sections produces a file the human still has to read carefully.

---

## 5. Risks

- **Prompt-injection surface widened.** `context.md` is now injected
  verbatim into every skill prompt. An operator who pastes untrusted
  content into it (e.g. a copy-paste from a GitHub issue body that
  contained a prompt-injection payload) hands that content to Claude
  with no fencing. Mitigation today: the file is explicitly documented
  as human-only territory. Future hardening: wrap the verbatim sections
  in the same fencing applied by `prompt_guard.fence_external_data`.
- **Memory-block growth can starve the rest of the prompt.** The
  agent-loop prompt already carries merge policy, PR guidelines, drift
  detection, deep research, etc. Adding 80 + 40 + ~25 lines of memory
  on top — every mission — eats into the prompt budget that future
  features will want.
- **Reverse-lookup against `projects.yaml` runs on every skill call.**
  Cheap today, but it does a `Path.resolve()` per configured project,
  which can stat the filesystem and follow symlinks. On instances with
  many projects on a slow or networked FS, this could become measurable.
- **Write-time dedup uses the lightweight model on the hot path.** Each
  PR-review-learning cycle spawns a Claude CLI call. If lightweight is
  rate-limited or down, the 15s timeout fires per cycle, slowing the
  loop. The fallback works, but the operator only finds out by reading
  stderr.
- **Legacy compact-state files are tolerated but never warned about.**
  A plain-hash state file means anti-thrash falls back to the weaker
  target-distance heuristic until the first successful compaction
  rewrites it. Silently degraded behavior.
- **Empty-section enforcement in the compaction prompt is advisory.**
  If the model emits `## Architecture notes\n\n## Gotchas\n- ...`,
  the empty header survives into `learnings.md` and pollutes future
  Jaccard scoring with section header tokens.
- **No upper bound on total memory size injected.** Three caps but no
  global budget. An operator who increases `max_relevant_learnings` to
  200 and fills `context.md` to the brim can produce a 320-line
  memory block. Skill prompts have no detection / clamp for this.

---

## 6. Improvement axes

### Near-term, low-cost

- **Log a warning when `KOAN_ROOT` is unset inside an instance context.**
  Distinguish "skill invoked standalone" (silent fallback is correct)
  from "skill invoked by Kōan but env is broken" (should yell).
- **Emit a structured stat on write-time dedup outcome.** Surface
  `{lessons_in, exact_dropped, semantic_dropped, lessons_appended,
  cli_failed}` to the journal so an operator can see whether the
  dedup pass is actually doing anything.
- **Token-aware caps for `context.md` / `priorities.md`.** Replace the
  80-line / 40-line caps with token caps using the provider's tokenizer
  (we already have `cli_provider` indirection for the model name).
- **Fence the verbatim sections.** Apply `prompt_guard.fence_external_data`
  to `context.md` and `priorities.md` content so an accidental
  prompt-injection payload is at least neutralized.
- **Cap the total memory block.** Add a `memory.max_block_lines` config
  knob that clamps the assembled block — drop learnings first
  (least-confident source), then truncate context, then priorities.
- **Telemetry on anti-thrash skips.** Increment a counter in the
  journal: how often does the guard fire vs. let through? Tunes the
  10% threshold from data.
- **Validate themed compaction output.** Parse the model's output;
  drop empty sections; warn when the model invents headers outside
  the allowed five.

### Medium-term

- **Embedding-based learnings recall.** Replace Jaccard with a small
  embedding model (e.g. `text-embedding-3-small` via the provider, or
  a local `sentence-transformers` model). Cache embeddings in
  `instance/.koan-embeddings.jsonl` and invalidate on
  `learnings.md` hash change. Bigger relevance lift than any cap-tuning.
- **Per-skill memory budget overrides.** `/review` may want more
  diff-related learnings; `/plan` may want more architecture context.
  Add `memory:` sub-keys per skill in `config.yaml`.
- **Memory-block diff in journal.** When the assembled block changes
  meaningfully between consecutive missions on the same project, log
  what was added/dropped — gives the operator visibility into "why
  did Claude suddenly know about X."
- **Combine compaction reasons.** Today anti-thrash is binary skip /
  run. A "lazy compaction" mode could merge only the section that
  grew (e.g. only `## Gotchas` if all new lessons landed there), which
  would defeat the 10% threshold without burning the full pass.
- **Cache `context.md` / `priorities.md` reads per process.** Re-read
  only on mtime change. Saves I/O for the worst-case future
  high-frequency skill dispatch.

### Long-term / structural

- **Promote memory to a first-class store.** Today everything is Markdown
  files. A SQLite store would let us index by embedding, tag by source,
  expire by age, and surface metrics — without losing the human-readable
  fallback (export to MD on demand).
- **Bidirectional curation.** Let the agent *propose* edits to
  `context.md` / `priorities.md` (e.g. "I noticed this project moved its
  CLI from Click to Typer — update context.md?") that the human accepts
  or rejects via Telegram. Today those files are strictly human-write.
- **Cross-project lessons.** A pattern learned on project A ("always run
  `make lint` before claiming a fix is done") often applies to project
  B. A `memory/global/cross-project-learnings.md` injected alongside
  the per-project block would close that gap. Needs a curation step to
  decide what's truly portable.
- **Replace the two-pass dedup with a streaming agent.** The current
  write-time dedup is "filter candidates against existing." A small
  agent that *merges* the new candidate into an existing entry
  ("update the threshold from 3 to 5") would be more useful than just
  dropping near-duplicates.

---

## 7. Files touched

| File | Change |
|------|--------|
| `koan/app/skill_memory.py` | **New** — shared helper module |
| `koan/app/prompt_builder.py` | Delegate `_get_learnings_section` to helper |
| `koan/app/plan_runner.py` | Inject memory in 3 paths (review loop, new, iterate) |
| `koan/app/review_runner.py` | Inject memory; score against title+body+diff |
| `koan/skills/core/fix/fix_runner.py` | Inject memory |
| `koan/skills/core/implement/implement_runner.py` | Inject memory |
| `koan/skills/core/{fix,implement,plan,review}/prompts/*.md` | Add `{PROJECT_MEMORY}` placeholder |
| `koan/app/memory_manager.py` | Anti-thrash guard + JSON state file |
| `koan/app/pr_review_learning.py` | Write-time semantic dedup |
| `koan/system-prompts/learnings-dedup.md` | **New** — dedup prompt |
| `koan/system-prompts/learnings-compaction.md` | Themed output sections |
| `koan/system-prompts/agent.md` | Document the three memory sources |
| `instance.example/config.yaml` | Document `memory.write_time_dedup` |
| `instance.example/memory/projects/_template/context.md` | Document auto-injection |
| `instance.example/memory/projects/_template/priorities.md` | Document auto-injection |

Tests: 21 new (`test_skill_memory`: 12, `test_pr_review_learning` dedup: 5,
`test_memory_manager` anti-thrash + state: 4). Full suite: 12,772 pass.
