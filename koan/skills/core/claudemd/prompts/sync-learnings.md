You are curating a project's CLAUDE.md — the authoritative reference AI
coding assistants read before touching **{PROJECT_NAME}**.

Kōan has accumulated the raw learnings below while working autonomously on
this project. Your job: distill the ones that belong in CLAUDE.md as
**permanent conventions**, and output nothing else.

## Raw Kōan learnings

{LEARNINGS}

## Current CLAUDE.md (for de-duplication)

{CURRENT_CLAUDE_MD}

## Rules

1. **Durable only.** Include a learning only if the rule holds regardless
   of whether any specific bug exists. Drop bug-specific quirks
   ("returns None when X", "#1234 fixed by…") — they become false context
   once patched.
2. **No overlap.** If the current CLAUDE.md already states a convention (even
   in different words), DO NOT repeat it.
3. **Generalize.** Rewrite each kept learning as a crisp, imperative
   convention a new contributor could follow. Merge near-duplicates.
4. **English only**, even if a source learning is in another language.
5. **No private identifiers.** Never emit internal/operator-private skill,
   agent, bot, project, or ticket-prefix names; describe the mechanism
   generically instead.
6. **Concise.** Prefer a short bulleted list grouped under `###`
   sub-headings when there are distinct topics. Aim for signal, not volume.

## Output

Output ONLY the markdown body (bullets / sub-headings) to place in the
managed block — no preamble, no code fences around the whole thing, no
"here is". If nothing qualifies after applying the rules above, output
exactly:

NO_DURABLE_LEARNINGS
