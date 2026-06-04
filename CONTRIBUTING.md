# Contributing to Kōan

Thanks for contributing! This guide covers how to set up your environment, make changes, and submit them.

## Development Setup

```bash
git clone https://github.com/Anantys-oss/koan.git
cd koan
make setup          # Create venv, install dependencies
```

Copy and customize the instance template:

```bash
cp -r instance.example instance
# Edit instance/config.yaml, instance/soul.md, and .env as needed
```

## Running Tests

```bash
# Full test suite (KOAN_ROOT must be set)
KOAN_ROOT=/tmp/test-koan make test

# Single test file
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_missions.py -v

# With coverage report
KOAN_ROOT=/tmp/test-koan make coverage
```

All tests must pass before submitting. CI runs against multiple Python versions — if it doesn't run on 3.11, it doesn't ship.

## Linting

```bash
make lint   # Runs ruff (PERF rules enforced; E/F/W/I/B are good hygiene)
```

Fix all lint errors before committing. Do not disable rules with `# noqa` unless there is a clear, documented reason.

## Code Conventions

See [CLAUDE.md](CLAUDE.md) for the full conventions. Key points:

- **Python 3.11+ only** — no syntax or stdlib introduced after 3.11.
- **No inline prompts** — LLM prompts go in `.md` files, loaded via `load_prompt()` or `load_skill_prompt()`. Reusable fragments belong in `koan/system-prompts/_partials/`.
- **Branch isolation** — Kōan creates `koan/*` branches, never commits to `main`.
- **Public artifacts stay generic** — no private operator identifiers in source code, comments, docstrings, tests, docs, or commit messages. Use placeholders: `my_toolkit`, `my_team`, `my_fix`, `@koan-bot`, `PROJ-NNN`.
- **No hyphens in skill names** — Telegram treats hyphens as word boundaries. Use underscores: `dead_code`, not `dead-code`.
- **Config via files, not env vars** — new features should use `config.yaml` / `projects.yaml` for configuration. Env vars are for secrets and deployment-specific settings.

## Adding a New Core Skill

Every core skill needs ALL of these. See the full checklist in [CLAUDE.md](CLAUDE.md) "Adding a new core skill" section.

1. Create `koan/skills/core/<skill_name>/SKILL.md` with frontmatter including `name`, `description`, `group`, `commands`, and `audience`.
2. Register in `skill_dispatch.py` if it runs via the agent loop.
3. Add to `docs/users/skills.md` in the appropriate category table.
4. Add to `docs/users/user-manual.md` in the appropriate tier section and quick-reference table.
5. Run tests — `TestCoreSkillGroupEnforcement` enforces the `group:` field.

See [koan/skills/README.md](koan/skills/README.md) for the full SKILL.md format and handler conventions.

## Documentation

**Before implementing**, inspect relevant docs with search tools. **After changing** user behavior, configuration, daemon flow, provider behavior, shared state, or safety boundaries, update the relevant docs in the same branch.

| Change type | Docs to update |
|---|---|
| User command / skill | `docs/users/user-manual.md` + `docs/users/skills.md` |
| Architecture / daemon | `docs/architecture/` |
| Provider behavior | `docs/providers/` |
| Messaging / tracker integration | `docs/messaging/` |
| Config / operations | `docs/operations/` + `instance.example/config.yaml` |
| Design decision / philosophy | `docs/design/decisions.md` |
| Security | `docs/security/` |

Prefer updating an existing page over adding a new one unless the topic is a new subsystem.

## Pull Request Process

1. Create a feature branch and make your changes.
2. Run `make lint` and `make test` — both must pass.
3. Update relevant documentation in the same branch.
4. Submit a PR against `main`.
5. The PR description should explain what changed and why.

## Release Process

Releases are cut from `main` when it's healthy and something worth shipping has landed. See [docs/operations/maint.md](docs/operations/maint.md) for the full procedure (`make release`).

## Questions?

- [Documentation index](docs/README.md)
- [User manual](docs/users/user-manual.md)
- [Architecture overview](docs/architecture/overview.md)
