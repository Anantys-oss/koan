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
recency. Dual-written alongside JSONL (best-effort — JSONL succeeds regardless
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
