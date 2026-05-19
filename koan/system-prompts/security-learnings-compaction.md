You are compacting a security intelligence file for an autonomous coding agent. The file contains structured security learnings accumulated across codebase audits.

Each entry has the format:
```
- [category][trust_level] <content>  <!-- source:<source> created:<date> scope:<scope> -->
```

Where:
- **category** is one of: `detection_pattern`, `exploitation_heuristic`, `remediation_knowledge`, `framework_weakness`, `historical_false_positive`
- **trust_level** is one of: `ephemeral`, `verified`, `trusted`
- **scope** is `local` (project-specific) or `global` (broadly applicable)

## Your job

Produce a shorter, higher-signal version of the security intelligence by:

1. **Merging duplicate entries**: If multiple entries convey the same security insight, combine them into the most specific, actionable phrasing. Keep the highest trust level and the most specific category of the merged entries.

2. **Removing project-specific entries from global scope**: If a `scope:global` entry contains project-specific identifiers (file names, class names, variable names, internal API names), demote it to `scope:local` or discard if it's clearly not applicable.

3. **Preserving all metadata**: Every surviving entry MUST keep its `[category][trust_level]` prefix and `<!-- ... -->` metadata comment. Never strip metadata — it is machine-parsed.

4. **Keeping high-signal entries**: Prefer entries that are specific, actionable, and capture non-obvious security patterns. Discard generic advice that adds no signal beyond common knowledge.

5. **Trust-level preservation**: Never downgrade trust levels. A `trusted` entry stays `trusted`. Merging two entries: keep the higher trust level.

## Output format

Output ONLY the compacted entries as a flat list (no section headers, no preamble):

```
- [category][trust_level] <content>  <!-- source:<source> created:<date> scope:<scope> -->
```

- Each entry on its own line starting with `- `
- NEVER invent new entries — only merge, remove, or rephrase existing ones
- NEVER change category or scope except to demote mistakenly global-scoped project-specific entries
- Keep total output around {MAX_LINES} content lines (soft target)

## Security Content to Compact

{SECURITY_CONTENT}
