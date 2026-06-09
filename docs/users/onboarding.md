# Onboarding Guide

The onboarding wizard is an interactive CLI tool that walks you through setting up Koan for the first time. It covers everything from prerequisites to your first launch.

## Quick Start

```bash
make install
```

`make koan` also runs the same onboarding wizard automatically on the first
interactive launch when no `instance/` is detected, or when a previous
onboarding checkpoint exists. You can run the wizard directly with
`make onboard`.

Use `--force` to restart from scratch:

```bash
make onboard ARGS="--force"
```

## What It Does

The wizard runs through 12 steps:

| Step | What it does | Files modified |
|------|-------------|----------------|
| 1. Prerequisites | Checks Python 3.11+, git, supported CLI providers, gh | — |
| 2. Instance init | Creates `instance/` from template and `.env` | `instance/`, `.env` |
| 3. Provider | Chooses Claude, Codex, Copilot, or local provider | `.env` |
| 4. Models | Sets provider-specific model defaults (accept or customize per role) | `instance/config.yaml` |
| 5. Virtual env | Runs `make setup` to install dependencies | `.venv/` |
| 6. Messaging | Configures Telegram, Slack, or Matrix credentials | `.env` |
| 7. Language | Sets preferred reply language | `instance/language.json` |
| 8. Personality | Chooses agent tonality (soul preset) | `instance/soul.md` |
| 9. Kōan workspace | Clones `https://github.com/Anantys-oss/koan` into `workspace/koan` | `workspace/koan/` |
| 10. GitHub | Configures gh auth and @mention support | `.env`, `instance/config.yaml` |
| 11. Deployment | Chooses terminal or systemd | — |
| 12. Verification | Shows summary and next steps | — |

Kōan is added as the default workspace project automatically. Add your own
repositories after setup with `/add_project <github-url>`.

## Resumable

Progress is saved to `.koan-onboarding.json` after each step. If the wizard is interrupted (Ctrl-C, error, network failure), re-run `make onboard` to continue from where you left off.

The checkpoint file is deleted automatically on successful completion.

During the terminal wizard, `Ctrl-R` resets onboarding progress and restarts the
flow from the welcome screen. This clears `.koan-onboarding.json`; it does not
delete private files such as `.env` or `instance/`.

## Personality Presets

During step 6, you can choose from five personality presets:

- **Sparring partner** (default) — analytical, direct, dry humor. Challenges your thinking.
- **Mentor** — patient, pedagogic, encouraging. Guides and teaches.
- **Pragmatist** — minimal, efficient, no-nonsense. Gets things done.
- **Creative** — playful, exploratory, lateral thinking. Suggests unexpected angles.
- **Butler** — formal, polished, deferential. Professional and respectful.

Presets are stored in `instance.example/soul-presets/`. You can customize `instance/soul.md` further after setup.

## Changing Settings Later

| Setting | How to change |
|---------|--------------|
| Language | `/language` command in Telegram |
| Personality | Edit `instance/soul.md` directly |
| Projects | Use `/add_project <github-url>` or edit `projects.yaml` |
| Messaging | Edit `.env` (KOAN_TELEGRAM_TOKEN, etc.) |
| GitHub | Edit `instance/config.yaml` github section |
| Budget/schedule | Edit `instance/config.yaml` |

## Non-Interactive Mode

If stdin is not a TTY (e.g., in CI), the wizard uses default values for all prompts. Set `NO_COLOR=1` to disable colored output.
