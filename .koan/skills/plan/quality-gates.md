# Koan-repo plan gates

Extra must-checks for `/plan` on this repository.

## Context section must include

- What `wiki/index.md` / docs / durable specs said for this topic, **or** an explicit “nothing relevant found”.
- Whether the work would touch `specs/components/**` or `specs/skills/**` (architectural surface).

## Plan quality

- Prefer the smallest change that satisfies the issue; call out out-of-scope ideas separately.
- Call out test strategy (`KOAN_ROOT`, mock boundaries) for code-changing plans.
- Never plan to commit private identifiers into the public tree.
