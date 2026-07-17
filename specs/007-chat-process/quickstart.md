# Quickstart / Validation: Dedicated Chat Process

Prerequisites: a configured Kōan instance (`instance/`), `KOAN_ROOT` set, Telegram
configured (or use `make say`).

## Run the stack

```bash
make start          # now launches run + awake + chat (+ ollama if configured)
make status         # chat process appears alongside run/awake
make logs           # tails logs/chat.log too
make chat           # (foreground) run only the chat process, for debugging
```

## Validate the core fix (User Story 1)

1. Queue a long mission so a provider subprocess is live (`.koan-active` present).
2. Send several chat messages via Telegram (or `make say m="hi"` repeatedly).
3. Expect: each message gets a coherent reply, in order; the
   "⚠️ I didn't get a response" failure does not appear; the mission keeps running.

## Validate fresh personality (User Story 2)

1. Send a chat message; note the tone.
2. Edit `instance/soul.md`.
3. Send another chat message — the reply reflects the edit, no restart.

## Validate graceful fallback (User Story 3)

1. `make stop` for chat only (or don't start it): send a chat message → still answered
   (inline worker-thread fallback), no user-visible difference.
2. Kill the chat process mid-flight → the bridge detects it and answers inline.

## Automated checks

```bash
make lint
KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_chat_context.py \
  koan/tests/test_chat_process.py \
  koan/tests/test_outbox_manager.py koan/tests/test_awake.py \
  koan/tests/test_active_mission.py -v
make test           # full suite
```

Expected: all green; the regression-lock tests confirm the dedicated path uses the same
`max_turns`/tools/`cwd` and performs the guard scan + both history writes.

## Rollback

The feature is optional: not starting `chat` (or `make stop`) reverts chat to the exact
prior inline behavior. The outbox Phase-1 skip only changes formatting *style* during
missions (fallback vs Claude-polished), never delivery.
