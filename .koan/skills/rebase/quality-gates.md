# Koan-repo rebase / conflict gates

Extra must-checks for `/rebase` (feedback apply, conflict resolution, CI fix) on this repository.

## Conflicts

- Do **not** “resolve” by deleting or emptying `specs/`, tests, workflows, or wiki bookkeeping without explicitly reporting what was dropped.
- Preserve both sides’ intent when possible; if policy forces a side, surface the choice in the actions log / PR comment.
- Prefer keeping durable contracts and test coverage over convenience.

## Commits and privacy

- Commit subjects and bodies: no private slash commands, bot names, Jira prefixes, or customer project names.
- Keep conventional, English subjects consistent with the repo.

## After rebase / CI fix

- Do not invent drive-by refactors unrelated to the conflict or CI failure.
- If CI fix touches API routes or core skills, remember OpenAPI regen and user-manual/skills.md obligations.
