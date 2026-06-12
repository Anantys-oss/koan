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

## Group chats

Kōan works in a group (or supergroup) as well as a 1:1 chat. **One instance
serves one chat at a time** — either a private chat or a group, not both.

### Setup

1. **Add the bot to the group.**
2. **Get the group's chat ID.** Send a message in the group, then run the
   `getUpdates` call from Step 2. The group ID is a **negative** number
   (e.g. `-1001234567890`). Set it as your chat ID:
   ```bash
   KOAN_TELEGRAM_CHAT_ID=-1001234567890
   ```
3. **Let the bot read every message** (see below).

### Privacy Mode — required to respond to every message

By default, Telegram bots run with **Privacy Mode ON**. In a group, a
privacy-mode bot only receives `/commands`, `@mentions`, and replies to its own
messages — Telegram **never delivers plain group messages** to it. This is a
Telegram-side restriction; no Kōan setting can bypass it.

To make the bot respond to every message (like a 1:1 chat), do **one** of:

- **Disable Privacy Mode**: message [@BotFather](https://t.me/BotFather) →
  `/setprivacy` → select your bot → **Disable**. Then **remove the bot from the
  group and re-add it** — privacy changes only take effect after re-adding.
  This is the most common gotcha: disabling without re-adding appears to do
  nothing.
- **Or promote the bot to administrator** in the group — admins receive all
  messages regardless of the privacy setting.

At startup Kōan probes this and tells you which case you're in. If the bot is
blocked, it logs a warning **and posts a remediation message into the group**:

```
[init] Chat type: supergroup — group mode active
[warn] Privacy Mode is ON — bot only sees /commands, @mentions, and replies in this group
[warn] Fix: @BotFather /setprivacy → Disable then re-add the bot, OR promote the bot to admin
```

Once fixed you'll instead see:

```
[init] Group mode: bot can read all messages ✓
```

> **Quick check**: even with Privacy Mode on, a `/help` typed in the group
> should get a reply — commands are always delivered. If it does, your chat ID
> is correct and Privacy Mode is the only remaining blocker.

## Architecture Notes

- **Polling**: Kōan polls the Telegram API every 3 seconds for new messages
- **No webhooks**: No public URL or reverse proxy needed — works from any network
- **Single chat**: Kōan only responds in the configured chat ID (ignores other
  chats). The chat ID may be a 1:1 chat or a group — see [Group chats](#group-chats).
