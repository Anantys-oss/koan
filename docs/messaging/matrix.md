# Matrix Setup Guide

This guide covers setting up Kōan with [Matrix](https://matrix.org) as the messaging provider. Kōan talks to a Matrix homeserver via the Client-Server HTTP API — no extra Python packages are required beyond `requests`.

## Prerequisites

- Access to a Matrix homeserver. You can use [matrix.org](https://matrix.org), a self-hosted Synapse/Dendrite/Conduit, or any compliant server.
- A dedicated Matrix account for the bot (recommended — don't reuse your personal account).
- An Element (or other Matrix client) login for the bot account, to invite it into the operating room.

## Step 1: Create a Bot Account

Either register a new account directly on the homeserver or use an existing dedicated account. The user ID will look like `@koan:matrix.org`.

## Step 2: Obtain an Access Token

The easiest way is to log in via Element with the bot account, then:

1. Open Element → **Settings → Help & About**
2. Scroll to the bottom and expand **Access Token**
3. Copy the token (long string starting with `syt_`, `mat_`, or similar)

Alternatively, use the `/login` API endpoint:

```bash
curl -XPOST -d '{
  "type": "m.login.password",
  "user": "koan",
  "password": "YOUR_BOT_PASSWORD"
}' "https://matrix.org/_matrix/client/v3/login"
```

The response contains an `access_token` field.

> **Security note:** The access token grants full account access. Treat it like a password — never commit it. If leaked, log out the session via Element (**Settings → Sessions**) to invalidate it.

## Step 3: Create or Choose a Room

Pick the room Kōan will operate in. Either:

- Create a new private room in Element and invite the bot.
- Use an existing room and invite the bot.

Get the room ID:

1. In Element, open the room
2. Click the room name → **Settings → Advanced**
3. Copy the **Internal room ID** (e.g., `!abcdefghijk:matrix.org`)

Make sure the bot account has joined the room (accept the invite from the bot's session, or call `/_matrix/client/v3/join/{roomId}`).

## Step 4: Configure Kōan

The recommended approach is to put Matrix settings in `instance/config.yaml`:

```yaml
messaging:
  provider: "matrix"
  matrix:
    homeserver: "https://matrix.org"
    user_id: "@koan:matrix.org"
    room_id: "!abcdefghijk:matrix.org"
    access_token: "syt_your_token_here"
```

> Treat `instance/config.yaml` like a secret file — it's gitignored by default. If you commit your `instance/` directory to a separate private repo, that's fine; never commit the access token to a public repo.

### Legacy: environment variables

The four `KOAN_MATRIX_*` env vars are still supported and override `config.yaml` when set. Use them only if you have a workflow built around `.env`:

```bash
# .env (legacy alternative)
KOAN_MESSAGING_PROVIDER=matrix
KOAN_MATRIX_HOMESERVER=https://matrix.org
KOAN_MATRIX_ACCESS_TOKEN=syt_your_token_here
KOAN_MATRIX_USER_ID=@koan:matrix.org
KOAN_MATRIX_ROOM_ID=!abcdefghijk:matrix.org
```

Precedence: env var > `config.yaml` value > error.

## Step 5: Start Kōan

```bash
make start
```

You should see in the logs:

```
[init] Messaging provider: MATRIX, Channel: !abcdefghijk:matrix.org
```

## How it works

- **Sending**: `PUT /_matrix/client/v3/rooms/{roomId}/send/m.room.message/{txnId}` with `msgtype: m.text`. Long messages are chunked to 4000 characters per event.
- **Receiving**: Long-polls `GET /_matrix/client/v3/sync` with a 30-second timeout. The first sync discards historical events and records the `next_batch` cursor; subsequent syncs return only new events.
- **Filtering**: Only `m.room.message` events with `msgtype: m.text` are surfaced. Messages sent by the bot's own user ID are ignored so it doesn't reply to itself.

## Troubleshooting

### "Missing required settings"

All four values (`homeserver`, `access_token`, `user_id`, `room_id`) must be set — either under `messaging.matrix` in `instance/config.yaml` or via the corresponding `KOAN_MATRIX_*` env vars.

### `[matrix] API error 401` / `403`

- The access token is invalid or has been revoked. Generate a new one (Step 2).
- The bot account isn't joined to the room. Accept the invite first.

### `[matrix] API error 404`

- The room ID is wrong, or the homeserver doesn't know about it.
- Ensure the room ID starts with `!` and includes the homeserver suffix (e.g., `!abc:matrix.org`).

### Bot replies to its own messages

- Double-check `KOAN_MATRIX_USER_ID` exactly matches the bot's user ID (including the leading `@` and the homeserver part).

### Encrypted rooms

This integration uses unencrypted Matrix rooms. End-to-end encryption (Olm/Megolm) is not implemented — using an E2EE room means messages will appear as undecryptable events. Either disable encryption on the room or create a fresh unencrypted room for the bot.
