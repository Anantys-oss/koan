# Telegram Setup Guide

This guide covers setting up Kōan with Telegram as the messaging provider.

> **Note**: Telegram is the default provider. If you've followed the standard `INSTALL.md` setup, you're already using Telegram — no additional configuration needed.

## Prerequisites

- A Telegram account

## Step 1: Create a Telegram Bot

1. Open Telegram, message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, choose a name and username
3. Copy the bot token (format: `123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

## Step 2: Get Your Chat ID

1. Open a chat with your new bot in Telegram and send any message (e.g., "hello")
2. Run:
   ```bash
   curl -s "https://api.telegram.org/botYOUR_TOKEN/getUpdates" | python3 -m json.tool
   ```
3. Look for `"chat": {"id": 123456789}` in the response — that number is your chat ID

## Step 3: Configure Environment

Edit your `.env` file:

```bash
# Telegram credentials (required)
KOAN_TELEGRAM_TOKEN=123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
KOAN_TELEGRAM_CHAT_ID=987654321
```

Optionally, you can explicitly set the messaging provider (defaults to Telegram):

```bash
KOAN_MESSAGING_PROVIDER=telegram
```

Or in `instance/config.yaml`:

```yaml
messaging:
  provider: "telegram"
```

## Step 4: Start Kōan

```bash
make start
```

You should see in the logs:
```
[init] Messaging provider: TELEGRAM, Channel: 987654321
```

## Troubleshooting

### Bot not responding

1. **Verify token**: `curl "https://api.telegram.org/botYOUR_TOKEN/getMe"` should return bot info
2. **Verify chat ID**: Make sure `KOAN_TELEGRAM_CHAT_ID` matches the ID from `getUpdates`
3. **Check logs**: `make logs` — look for `[error]` entries
4. **Restart**: `make stop && make start`

### "KOAN_TELEGRAM_TOKEN not set" error

Your `.env` file is missing or the variable name is wrong. Double-check the format:
- **Token**: Starts with digits, then `:`, then alphanumeric string (e.g., `123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
- **Chat ID**: Numeric only, no letters or `@` symbol (e.g., `987654321`)

### Messages not delivered

- Telegram has a 4000-character limit per message. Long messages are auto-chunked.
- Duplicate messages within 5 minutes are flood-protected (first duplicate triggers a warning, subsequent ones are silently dropped).

## Architecture Notes

- **Polling**: Kōan polls the Telegram API every 3 seconds for new messages
- **No webhooks**: No public URL or reverse proxy needed — works from any network
- **Single chat**: Kōan only responds in the configured chat ID (ignores other chats)
