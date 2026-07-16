---
type: doc
title: "Fake CLI Provider (test/dev only)"
description: "Fail-closed no-op CLI provider used for deterministic, offline tests of the skill pipeline; refuses to run unless KOAN_ALLOW_FAKE_PROVIDER=1 is set."
tags: [providers]
created: 2026-07-15
updated: 2026-07-15
---

# Fake CLI Provider

> ⚠️ **Test/dev only.** The `fake` provider **never invokes a real LLM**. It
> exists so deterministic, offline end-to-end tests of the skill pipeline can
> select a CLI flavor like any other provider without burning API quota or
> depending on a network. Do **not** enable it on a production instance.

## Fail-closed by design

Selecting `fake` is not enough to run it. `FakeProvider` is **fail-closed by
construction**: instantiating it raises `FakeProviderNotAllowed` unless the
environment explicitly opts in with:

```bash
KOAN_ALLOW_FAKE_PROVIDER=1   # also accepts: true / yes / on
```

Because the guard lives in the constructor, **every** provider-resolution path
(global provider, per-role `cli.<role>`, launch/auth fallback, and name-based
lookup used by post-run error classification) fails closed identically. When
`fake` is selected without the flag, Kōan **errors with an actionable message
and never silently falls back to a real provider**:

```
The 'fake' CLI provider was selected but KOAN_ALLOW_FAKE_PROVIDER=1 is not set.
This provider is for tests/dev only and never invokes a real LLM. Refusing to
run so Kōan does not silently fall back to a real provider. ...
```

## Selection

Once `KOAN_ALLOW_FAKE_PROVIDER=1` is set, `fake` is selectable like any other
flavor — via the global provider, per-role config, or a project override:

```yaml
# config.yaml — global (⚠️ test/dev only)
cli_provider: "fake"
```

```bash
# env (⚠️ test/dev only)
KOAN_ALLOW_FAKE_PROVIDER=1
KOAN_CLI_PROVIDER=fake
```

```yaml
# config.yaml — per role (⚠️ test/dev only)
cli:
  default:
    mission: fake
```

## Behavior

- `binary()` returns the POSIX no-op `true` (never `claude`), so the built
  command runs harmlessly and produces empty output.
- `is_available()` is always `True` — no external binary is required.
- `has_api_quota()` is `False`, so budget gating is disabled.
- `build_command()` is a no-op stub: inputs are accepted and dropped.

**Smart response routing** (canned/scripted responses so a skill sees a
deterministic answer) is intentionally **out of scope** for this foundation and
lands in a follow-up. Until then the provider returns empty/minimal output.

The provider is **hidden from the dashboard provider dropdown** — it is flagged
`test_only`, so UI-facing pickers (`selectable_providers()`) exclude it while it
remains in the registry for name-based lookup and config validation. It is still
selectable via `KOAN_CLI_PROVIDER=fake` or `cli_provider: fake` (with the allow
flag set).

## Production impact

None for normal instances — the provider cannot run without the explicit
`KOAN_ALLOW_FAKE_PROVIDER=1` opt-in, so a stray `cli_provider: fake`
misconfiguration errors loudly rather than degrading a real instance.
