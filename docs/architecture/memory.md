# Memory Architecture

Koan keeps memory as Markdown files and a JSONL truth log under
`instance/memory/`. A SQLite FTS5 secondary index (`memory.db`) provides
ranked retrieval over the JSONL log.

## Memory Types

- Global memory captures cross-project summaries and operator preferences.
- Project memory lives under `memory/projects/{name}/` and stores context,
  priorities, learnings, and related project-specific material.
- Journals under `instance/journal/` capture daily runtime output and reflection.

## Storage Layers

### JSONL Truth Log (`memory/log.jsonl`)

Append-only log of all memory entries (sessions, learnings, etc.). This is the
source of truth — all entries are written here first with `fcntl.flock(LOCK_EX)`
for concurrent safety.

### SQLite FTS5 Index (`memory/memory.db`)

A read-optimized projection of the JSONL log. Provides BM25-ranked full-text
search so mission-relevant entries surface in agent prompts instead of pure
recency. Ranking is confidence-weighted: BM25 `rank` is scaled by the entry's
`confidence` (`EXTRACTED`→1.0, `INFERRED`→0.75, `AMBIGUOUS`→0.4, numeric values
used directly, absent→0.9) so a high-confidence match outranks a marginally
better-matching but low-confidence one. Entries without confidence keep pure
BM25 order. Dual-written alongside JSONL (best-effort — JSONL succeeds regardless
of SQLite errors). Populated by a dedicated, always-run startup step
(`startup_manager.index_memory_sqlite`) that bulk-indexes existing JSONL entries.
This step is self-gated: `migrate_jsonl_to_sqlite()` only runs when `memory.db`
is missing or empty, so it is cheap and idempotent on every startup. It is kept
separate from the markdown→JSONL migration (which short-circuits on the
`.migration_done` sentinel) so that already-migrated instances still get indexed.

WAL mode is enabled for concurrent read access from both `run.py` and `awake.py`.
If `memory.db` is deleted or corrupted, all operations gracefully fall back to
JSONL, and the next startup rebuilds the index from the truth log.

When FTS5 is not compiled into the Python sqlite3 build, all search functions
short-circuit and the system operates in JSONL-only mode.

## Read Paths

The agent loop, skill prompts, reflection flows, and formatting flows can inject
memory into prompts. Memory inclusion should remain budget-aware and should use
existing helpers instead of ad hoc file reads.

`read_memory_window()` accepts an optional `query_text` parameter. When provided,
it uses two-phase retrieval: (1) FTS5-matched entries ranked by BM25, (2) recency
fill for remaining slots. When empty, it falls back to JSONL tail.

Learnings filtering uses FTS5 via `search_learnings()` with Jaccard fallback
when SQLite is unavailable.

### Observability

Each successful FTS5 read emits a usage line so the index is visible in normal
operation (not only on error). These are routed to **stderr** — which the
process launcher merges into `logs/run.log` — because the read paths also run
inside CLI subprocess runners whose stdout carries JSON/transcript data:

```
[koan] [memory] FTS5 surfaced 5/5 entries for koan (5 ranked match, 0 recency fill) — query='...'
[koan] [memory] FTS5 selected 3/35 learnings for koan — task='...'
```

Absence of these lines (with a clean error log) means a read happened via the
recency/Jaccard fallback. Failures still log at `WARNING` (e.g.
`[memory_db] search_entries failed`, `FTS5 retrieval failed, falling back to JSONL`).

## Entry Schema

Each JSONL entry has four required and four optional fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ts` | ISO8601 string | yes | UTC timestamp of entry creation |
| `type` | string | yes | Entry type: `"session"`, `"learning"` |
| `project` | string or null | yes | Project name, or null for global entries |
| `content` | string | yes | Entry text (capped at 2000 chars) |
| `source_skill` | string | no | Skill that produced this entry (e.g. `"review"`, `"fix"`) |
| `tags` | list of strings | no | Freeform classification tags |
| `confidence` | float 0.0–1.0 or label (`EXTRACTED`/`INFERRED`/`AMBIGUOUS`) | no | Confidence of the observation; weights FTS5 ranking |
| `expires_at` | ISO8601 string | no | Auto-expiry timestamp; entry is pruned after this time |

Optional fields are omitted from the JSON when not set (not stored as null).
Existing entries without the new fields work unchanged — they get empty defaults
in SQLite and are treated as having no skill, no tags, no expiry.

`source_skill` enables skill-aware retrieval: `read_memory_window(current_skill="review")`
boosts entries from the same skill. `expires_at` is enforced by both `prune_memory_log()`
(JSONL side) and `search_entries()`/`recent_entries()` (SQLite side).

## Write Paths

Memory is updated by session summaries, PR review learning, post-mission
reflection, explicit commands, and compaction flows. Write paths should preserve
human-authored files and avoid turning generated learnings into duplicated or
contradictory noise.

`append_memory_entry()` dual-writes to both JSONL and SQLite.
`prune_memory_log()` mirrors deletions to SQLite.

## Compaction

Compaction and deduplication are prompt-backed operations. They should be
bounded, reversible enough for review, and documented when their output format
changes because future prompts and agents use that structure as context.

See [Memory Injection](../design/memory-injection.md) for design notes.
