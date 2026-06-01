# Discord Messaging Provider

Kōan can use Discord as its messaging bridge via the Discord REST API. No WebSocket/Gateway connection is required — the provider uses REST polling, matching the latency profile of the existing Telegram bridge (~3 second polling interval).

## Prerequisites

- A Discord account with access to the target server
- A Discord server (guild) where you have permission to add bots
- Developer Mode enabled in Discord client settings (User Settings → Advanced → Developer Mode)

## Setup

### 1. Create a Discord Application and Bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. "Kōan"), and confirm
3. In the left sidebar, click **Bot**
4. Click **Add Bot** → **Yes, do it!**
5. Under **Token**, click **Reset Token** → copy the token and store it securely
6. Disable **Public Bot** unless you intend to share it

### 2. Set Bot Permissions

Still on the Bot page, under **Privileged Gateway Intents**, enable:
- **Message Content Intent** — required to read message text via REST polling

### 3. Invite the Bot to Your Server

1. In the sidebar, click **OAuth2 → URL Generator**
2. Under **Scopes**, check `bot`
3. Under **Bot Permissions**, check:
   - **View Channels**
   - **Send Messages**
   - **Read Message History**
4. Copy the generated URL, open it in your browser, and invite the bot to your server

### 4. Get the Channel ID

1. In Discord, right-click the channel you want Kōan to use
2. Click **Copy Channel ID**
3. Save the numeric ID (e.g. `123456789012345678`)

## Configuration

### Option A: Environment variables (recommended for secrets)

Add to your `instance/.env`:

```bash
KOAN_DISCORD_BOT_TOKEN=your_bot_token_here
KOAN_DISCORD_CHANNEL_ID=123456789012345678
```

Then set the provider:

```bash
KOAN_MESSAGING_PROVIDER=discord
```

### Option B: config.yaml

```yaml
messaging:
  provider: "discord"
  discord:
    bot_token: "your_bot_token_here"   # or use KOAN_DISCORD_BOT_TOKEN env var
    channel_id: "123456789012345678"   # or use KOAN_DISCORD_CHANNEL_ID env var
```

Environment variables take precedence over config.yaml values when both are set.

## How It Works

- **Polling**: Every ~3 seconds, Kōan calls `GET /channels/{id}/messages?after={last_id}` to fetch new messages
- **Sending**: Kōan calls `POST /channels/{id}/messages` for each outgoing message
- **Chunking**: Messages longer than 2000 characters (Discord's limit) are split into sequential chunks
- **Cursor**: The last seen message snowflake ID is stored in memory; on startup, only the current latest message ID is fetched to avoid replaying history
- **Bot mentions**: `<@BOT_ID>` prefixes are stripped before passing text to the command classifier

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` | Invalid bot token | Regenerate token in Developer Portal → Bot |
| `403 Forbidden` | Bot lacks channel permissions | Re-invite with correct permissions (View Channels, Send Messages, Read Message History) |
| `404 Not Found` | Wrong channel ID | Right-click channel → Copy Channel ID with Developer Mode enabled |
| `Missing Message Content Intent` | Intent not enabled | Developer Portal → Bot → enable Message Content Intent |
| Bot not receiving messages | Bot not in server | Use OAuth2 URL Generator to invite it |

## Notes

- **Guild ID not required**: For basic REST-only operation, only `channel_id` is needed. The bot accesses the channel directly via its ID.
- **Reactions**: Discord REST polling does not push reaction events in real time. Reaction-based deduplication (used by `github_notifications.py`) gracefully falls back to no-op when reactions are unavailable.
- **Rate limits**: Discord allows 50 requests/second globally and 5 messages/second per channel. Kōan uses `retry_with_backoff` with `Retry-After` header support for 429 responses.
