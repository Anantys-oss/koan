# Research: Repo-level `.koan/config.yaml` — `review.always_check`

Phase 0 decisions. All NEEDS CLARIFICATION from the plan's Technical Context are
resolved here.

## D1 — Config file location & reader home

**Decision**: Read `<project_path>/.koan/config.yaml` via a new
`project_koan.read_koan_config(project_path) -> dict`, with a typed accessor
`project_koan.get_review_always_check(project_path) -> list[str]`.

**Rationale**: `koan/app/project_koan.py` is already the sole reader of the target
repo's `.koan/` steering tree (`read_general_koan_md`, `read_skill_instructions`). It
owns the absent/blank/unreadable conventions (`_read_or_empty`, `_cap`) and the
`log_context_load` stderr announcement. Placing the config reader here keeps a single
`.koan/` read surface (Principle VI) and reuses the fail-safe file handling. It is
distinct from KOAN_ROOT's `instance/config.yaml` (read by `config.py`) — a different
file for a different scope.

**Alternatives considered**: a new `repo_config.py` module — rejected as needless
surface (Principle VII); extending `config.py` — rejected because that module is scoped
to the operator's `instance/config.yaml`, not the target repo.

## D2 — Parsing & fail-safe validation

**Decision**: `read_koan_config` does `yaml.safe_load` on the file text; on any
exception (parse error, OSError) or a non-mapping top-level value it returns `{}` and
logs one diagnostic. `get_review_always_check` reads `config.get("review", {})
.get("always_check", [])`, then keeps only `str` items after stripping, discarding
non-list values (returns `[]`) and non-string items; blanks are dropped.

**Rationale**: A target repo's config is semi-untrusted (Principle V): validate at the
edge, fail safe to "no pins", never abort. `yaml.safe_load` (never `load`) avoids
arbitrary object construction. Returning `[]`/`{}` on every bad shape makes the absent
and malformed cases converge to the byte-identical no-op the spec requires (SC-002/003).

**Alternatives considered**: raising on malformed config — rejected (violates
fail-safe FR-006, would let a bad repo file break an unrelated review).

## D3 — Pattern-matching semantics

**Decision**: A changed file at repo-relative path `p` matches pattern `pat` iff
`fnmatch.fnmatch(p, pat)` OR `fnmatch.fnmatch(os.path.basename(p), pat)`. Case-sensitive
(`fnmatch.fnmatch` is case-normalizing on some platforms, so use `fnmatch.fnmatchcase`
against both `p` and its basename for deterministic, case-sensitive behavior across
Linux/macOS). Matching is applied to the file path parsed out of each `diff --git`
block.

**Rationale**: Users expect `SKILL.md` to match `plugins/x/SKILL.md` (basename match)
and `*.md` to match any Markdown file at any depth. `fnmatch`'s `*` already spans `/`,
so `*.md` on the full path covers nested files; the basename arm covers bare-filename
patterns like `SKILL.md`. `fnmatchcase` gives identical results on macOS (case-
insensitive FS) and Linux, matching FR-007's "case-sensitive" requirement.

**Helper placement**: put a small `path_matches_any(path, patterns) -> bool` (plus the
per-file predicate) in `koan/app/diff_compressor.py`, since that module already parses
per-file blocks and is where the primary pin decision lives; `truncate_diff_with_skips`
in `utils.py` imports and reuses it (utils may import diff_compressor — no cycle, since
diff_compressor does not import utils). This avoids duplicating glob logic across the two
skip paths (Principle VII).

**Alternatives considered**: full `**` recursive globbing via `pathlib.PurePath.match`
or a glob library — rejected as unnecessary (`fnmatch` `*` already spans separators) and
a new dependency (Principle VII). Regex — rejected as a worse UX for repo owners.

## D4 — Pinning in `compress_diff` (primary path)

**Decision**: Add `pinned_patterns: Optional[list[str]] = None`. Compute a per-file
`is_pinned` flag; extend the existing sort key so pinned files sort first:
`key = (0 if is_pinned else 1, -_language_priority(path), token_estimate)`. The greedy
whole-file/partial-hunk inclusion loop is unchanged — pinned files simply consume budget
first, so small files like `SKILL.md` are always fully included. The existing "single
massive file → force first hunk" safety and the `(partial)` marker are untouched. A
pinned file that still cannot fit whole degrades to partial hunks exactly like today's
oversized-file behavior (documented best-effort, spec Edge Cases).

**Rationale**: Reordering the inclusion priority is the minimal change that satisfies
"never fully skipped while budget remains" (FR-003) without a parallel code path or a
budget increase (the size ceiling stays the single source of truth). It composes with
language priority: among pinned files, higher-priority languages still come first.

**Alternatives considered**: a separate "reserve budget for pinned files up front"
pass — rejected as more complex for no additional guarantee on realistically-small
pinned files; exempting pinned files from the budget entirely — rejected because it
could blow the model context window (defeats the size guard, Principle VII).

## D5 — Pinning in `truncate_diff_with_skips` (compressor-off / char backstop)

**Decision**: Add the same `pinned_patterns` parameter. Before the greedy keep-loop,
stably partition the `diff --git` blocks into (pinned, rest) preserving each group's
original order, then run the existing greedy fit over `pinned + rest`. Footer/skip
accounting is unchanged; pinned blocks are simply offered budget first.

**Rationale**: Symmetry with D4 so the guarantee holds when the compressor is disabled
(FR-005). Stable partition keeps output deterministic and diff-order-preserving within
each group.

## D6 — Coverage-note honesty (tighten the invariant)

**Decision**: No change to `_build_coverage_note`'s signature or logic. Because pinning
removes included files from the compressor/backstop `skipped_files` output, those files
never enter the note. The note continues to list only genuinely-omitted files, and the
`{SKIPPED_FILES}` prompt slot and posted body stay derived from the one value
(FR-004, Principle VI). The contract text in `specs/components/skills.md` is tightened to
state that pinned-and-included files are absent from the note.

**Rationale**: The single-source-of-truth note already reflects the compressor's actual
skip list; feeding it a pin-aware skip list is automatically honest. No divergence risk.

## D7 — Observability logging

**Decision**: When ≥1 file is pinned, `build_review_prompt` emits one line via the
existing `log("review", ...)` channel (same channel as the current "Diff compressed —
N file(s) skipped" line), e.g. `Pinned N file(s) via .koan review.always_check: <names>`.
No line when there are no pins (absent-config byte-identical no-op). Optionally reuse
`project_koan.log_context_load` for a `[context] Detected .koan/config.yaml ...` line
when the config is non-empty, matching the existing `.koan/` load-announcement pattern.

**Rationale**: FR-011 — an operator watching `make logs` can confirm the config took
effect, consistent with how `.koan/KOAN.md` and `.koan/skills/` loads are already
announced.

## D8 — Safety caps

**Decision**: Cap honored patterns at **100** and per-pattern length at **200** chars
(constants in `project_koan.py`). Excess/oversized patterns are dropped with a single
diagnostic. The reader's overall file read reuses the existing `.koan/` handling (no new
size cap needed for a small YAML; `_read_or_empty` already tolerates unreadable files).

**Rationale**: FR-012 — bound worst-case matching work (files × patterns) so a
pathological repo config cannot degrade review latency. 100 patterns is far above any
realistic need.

**Alternatives considered**: no cap — rejected (untrusted-input hardening, Principle V).

## D9 — Sample config location

**Decision**: Ship the committed sample as **`docs/reference/koan-config.sample.yaml`**
(a reference asset next to the docs), and also show the full annotated sample inline in
`docs/users/koan-md.md`. It demonstrates `review.always_check` with generic placeholder
patterns and includes the identified future keys as commented-out, inert examples.

**Rationale**: The sample is documentation, not live runtime config, so it belongs under
`docs/`, not `instance.example/` (which mirrors the operator's KOAN_ROOT, a different
scope) and not at the repo root (koan itself is not a review *target* that needs pins).
`docs/reference/` is the established home for reference assets. FR-010.

**Alternatives considered**: `instance.example/.koan/config.yaml` — rejected: `.koan/`
lives in *target* repos, not KOAN_ROOT; placing it there miscommunicates the scope.
Repo-root `.koan/config.yaml` in koan itself — rejected: it would be a live config on
koan's own repo, changing koan's self-reviews as a side effect.

## D10 — Future extension points (documented, NOT implemented)

Identified repo-level review knobs to name in docs/spec as the `.koan/config.yaml`
surface grows (FR-008). None are implemented in this feature:

- **`review.never_check`** — inverse of `always_check`: globs whose matching files are
  intentionally skipped (generated/vendored files, lockfiles, `dist/`), letting a repo
  owner suppress noise. Symmetric machinery; deferred to keep this PR one-concern.
- **`review.pause_label`** — per-repo override of the global `PauseReview` label
  (`get_review_pause_label()` today reads operator config only).
- **`review.default_focus`** — repo-level default focus passes (e.g. always run the
  silent-failure-hunter `--errors` pass), so a repo can opt into deeper reviews without
  per-invocation flags.
- **`review.compressor_token_budget`** — per-repo override of the diff-size budget, for
  repos that routinely need larger review context.

These are enumerated so readers understand the file is an extensible surface; their
concrete semantics are out of scope here.
