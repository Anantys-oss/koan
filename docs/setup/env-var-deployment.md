# Environment-variable-only deployment

Kōan does not require a hand-written `.env` file. On platforms that inject
configuration as process environment variables — Railway, Docker, Kubernetes,
systemd `Environment=` — you can run Kōan without ever authoring a `.env`.

## How it works

When the required `KOAN_*` settings are present in the process environment, the
onboarding `Initialize instance` step does not fail on a missing `env.example`.
Instead `create_env_file()` synthesizes a `.env` from the environment
(`write_env_from_environment()`), mirroring the platform variables into a
`0600` file, and proceeds. `load_dotenv()` then layers that file on top of
`os.environ` using `setdefault`, so injected environment variables always take
precedence.

The "required config present" check is `app.railway.required_env_present()` —
satisfied when an auth token (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`),
a GitHub token (`KOAN_GH_TOKEN`/`GH_TOKEN`), and the Telegram pair
(`KOAN_TELEGRAM_TOKEN` + `KOAN_TELEGRAM_CHAT_ID`) are all in the environment.
When neither a template nor the required env vars are available,
`create_env_file()` returns `False` and onboarding still fails loudly rather
than masking a misconfiguration.

`env.example` remains a template for interactive local setup and is optional in
containerized / PaaS deploys.

## Precedence

1. Process environment variables (highest priority — always win).
2. Values in the synthesized `.env` (only fill gaps via `os.environ.setdefault`).

This means you can set everything via environment variables and let onboarding
mirror them into `.env` for you.
