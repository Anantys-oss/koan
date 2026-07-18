# Koan-repo PR pipeline gates

Extra must-checks for `/pr` (feedback → refactor → quality review) on this repository.

## Scope

- Address review feedback; avoid unrelated drive-by changes.
- Do not force-push unless the human asked or the pipeline’s normal update requires it.

## Before claiming done

- Re-check privacy on **added** lines.
- Python changes: lint-clean mindset; no new inline LLM prompts.
- If feedback required API or core-skill doc updates, include them in the same push.
