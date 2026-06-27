# Artifact DB harness

`koan/app/artifact_db.py` is the shared harness for migrating markdown/JSONL
artifacts to a SQLite projection. The **file remains the source of truth**;
SQLite is a rebuildable read index (same model as `memory_db.py`).

This issue ships the harness + tests + docs only — **no live artifact path is
migrated here** (those are the downstream migration issues).

## Schemas

Five artifacts are declared in `ARTIFACT_SCHEMAS` as `TableSpec`/`ColumnSpec`
dataclasses: `missions`, `journal_entries`, `memory_entries`,
`outbox_messages`, `audit_log`. Each spec owns its `CREATE TABLE` DDL, so the
dataclass declaration is the single source of truth for the column set — there
is no persisted schema-mapping file to drift out of sync. The `memory_entries`
columns are pinned to `memory_db._EXPECTED_COLUMNS` so the two indexes agree.

## API

- `connect(path)` — open a connection; returns `None` on any DB error.
- `create_tables(conn)` — idempotent `CREATE TABLE IF NOT EXISTS` for all specs.
- `verify_schema(conn, table)` — drift check against `PRAGMA table_info`; returns
  `{in_sync, missing, unexpected}`. `in_sync` is `None` when the table does not
  exist yet (safe to call before `create_tables`).
- `dual_write(records, file_writer=, conn=, table=)` — writes the authoritative
  file first, then mirrors it to the DB best-effort. The file writer rewrites the
  whole artifact, so the projection is **truncated and rebuilt** in one
  transaction (not appended) — otherwise rows accumulate across rewrites. A DB
  failure is logged, rolled back, and flags the projection **dirty** so later
  reads fall back to the file; it is never propagated. A `file_writer` failure
  propagates (the write genuinely failed).
- `read_from_db_or_file(conn, table, file_reader=, order_key=None)` — reads the
  DB when populated and in-sync, else parses the file; preserves file-parse
  ordering so a consumer can't tell which source served the read. A dirty
  projection (last write failed to mirror) always falls back to the file.
  `order_key` is validated against the declared columns before interpolation —
  an unknown key is ignored (rowid order) rather than injected into SQL.

A `_artifact_meta` bookkeeping table tracks the dirty flag per artifact so the
"file is source of truth" guarantee holds even when a projection write fails
midway.

Every function catches `sqlite3.DatabaseError` and degrades gracefully — when
sqlite is unavailable, reads fall back to the file and writes still write the
file.

## Backward-compatibility timeline

1. **Dual-read** — read the DB when populated, else parse the file. No write
   change.
2. **Dual-write** — keep writing the authoritative file; project to the DB
   best-effort.
3. **DB-authoritative (opt-in, far future)** — per artifact only, and never the
   default for human-inspectable files like `missions.md` (you can `cat` a
   markdown file; you cannot `cat` a sqlite blob).
