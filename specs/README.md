# Kōan Specs

This directory is the **single source of truth for Kōan's design**. Specs capture
*why a component exists, what contract it upholds, and what changes if you touch it.*
They are the heart of the application: read them before implementing, update them
after implementing.

## Specs vs Docs

| | `specs/` | `docs/` |
|---|---|---|
| Question answered | "Why this design? What breaks if I change it?" | "How do I use this?" |
| Audience | Developers changing the code | Operators and users |
| Content | Contracts, invariants, integration points, known debt | Setup, config reference, feature guides |
| Stability | Changes when **design** changes | Changes when **behavior/UX** changes |

Specs and docs coexist — they do not replace each other. When a feature changes
*behavior*, update `docs/`. When it changes *design or contracts*, update `specs/`.
Most non-trivial changes touch both.

## Layout

```
specs/
├── README.md                     # this file — index + conventions
├── components/                   # one spec per architectural module group
│   ├── core.md                   # missions, config, constants, utils, logging
│   ├── agent-loop.md             # run.py pipeline, iteration, execution, finalize
│   ├── bridge.md                 # awake.py Telegram bridge + command handlers
│   ├── providers.md              # CLI provider abstraction (claude/cline/copilot)
│   ├── git-github.md             # git sync, auto-merge, gh wrapper, webhooks
│   ├── issue-tracking.md         # provider-neutral issue tracker (GitHub/Jira)
│   ├── skills.md                 # skills registry + dispatch system
│   └── web.md                    # dashboard (Flask) + REST API
└── skills/                       # one spec per skill
    ├── SKILL_SPEC_TEMPLATE.md    # copy this to author a new skill spec
    ├── review.md
    ├── implement.md
    └── ...
```

## Naming conventions

- **Component specs**: `specs/components/<group>.md`, kebab-case. A "group" maps to
  one of the module clusters in `CLAUDE.md`'s *Key modules* section.
- **Skill specs**: `specs/skills/<skill-name>.md`, matching the skill's directory
  name under `koan/skills/core/<skill-name>/` (underscores, never hyphens).
- One concern per spec. If a component spec exceeds ~300 lines, split it.

## Spec discipline (the rule that makes this matter)

This is mirrored in `CLAUDE.md` under *Specs discipline* and is **mandatory**:

1. **Before implementing** a feature or refactor, read the relevant component spec
   (and any skill spec you are touching). The spec tells you the contract you must
   not silently break.
2. **After implementing**, update the spec to reflect the new design — new types,
   changed integration points, resolved or newly-introduced debt. A PR that changes
   a component's contract without updating its spec is incomplete.
3. **No spec yet?** If you touch a component or skill that has no spec, write one
   using the relevant template. Phase 1 ships specs for the highest-impact pieces;
   the rest are added on-demand as they are touched.

## Coverage status (phase 1)

Phase 1 establishes the structure and the exemplars. Component specs cover the eight
module groups end-to-end. Skill specs cover the ten highest-impact skills as
templates for the remaining ~80, which are filled in on-demand.

| Area | Status |
|---|---|
| Component specs (8 groups) | ✅ phase 1 |
| Skill spec template | ✅ phase 1 |
| Skill specs | 🟡 10 of ~80 (on-demand thereafter) |
| Spec-driven refactoring | ⬜ enabled, not yet exercised |
