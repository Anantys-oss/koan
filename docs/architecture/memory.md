# Memory Architecture

Koan keeps memory as Markdown files under `instance/memory/`. There is no memory
database in the default design.

## Memory Types

- Global memory captures cross-project summaries and operator preferences.
- Project memory lives under `memory/projects/{name}/` and stores context,
  priorities, learnings, and related project-specific material.
- Journals under `instance/journal/` capture daily runtime output and reflection.

## Read Paths

The agent loop, skill prompts, reflection flows, and formatting flows can inject
memory into prompts. Memory inclusion should remain budget-aware and should use
existing helpers instead of ad hoc file reads.

## Write Paths

Memory is updated by session summaries, PR review learning, post-mission
reflection, explicit commands, and compaction flows. Write paths should preserve
human-authored files and avoid turning generated learnings into duplicated or
contradictory noise.

## Compaction

Compaction and deduplication are prompt-backed operations. They should be
bounded, reversible enough for review, and documented when their output format
changes because future prompts and agents use that structure as context.

See [Memory Injection](../design/memory-injection.md) for design notes.
