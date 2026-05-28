# Slack Setup Guide

This guide covers setting up Kōan with Slack as the messaging provider. Slack uses Socket Mode for real-time bidirectional communication.

## Prerequisites

- A Slack workspace where you have permission to install apps (or can request admin approval)

## Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From scratch**
3. Name it (e.g., "Kōan") and select your workspace
4. Click **Create App**

## Step 2: Enable Socket Mode

1. In your app settings, go to **Settings** → **Socket Mode**
2. Toggle **Enable Socket Mode** to ON
3. When prompted, create an App-Level Token:
   - Name: `koan-socket` (or anything descriptive)
   - Scope: `connections:write`
4. Click **Generate** and copy the token (starts with `xapp-`)

## Step 3: Add Bot Token Scopes

1. Go to **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**
2. Add these scopes:

   | Scope | Purpose |
   |-------|---------|
   | `chat:write` | Send messages |
   | `channels:history` | Read messages in public channels |
   | `groups:history` | Read messages in private channels |
   | `im:history` | Read direct messages |
   | `app_mentions:read` | Respond to @mentions |

3. Go to **Event Subscriptions** → Enable Events
4. Under **Subscribe to bot events**, add:
   - `message.channels`
   - `message.groups`
   - `message.im`
   - `app_mention`

## Step 4: Install App to Workspace

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace** (or **Request to Install** if admin approval is required)
3. Authorize the requested permissions
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

## Step 5: Get Your Channel ID

1. In Slack, right-click on the channel where Kōan should operate
2. Click **View channel details**
3. At the bottom of the panel, copy the **Channel ID** (e.g., `C01234ABCD`)

## Step 6: Invite Bot to Channel

In the Slack channel, type:
```
/invite @koan
```
(Replace `@koan` with your bot's display name)

## Step 7: Install Dependencies

```bash
pip install 'slack-sdk>=3.27'
# Or add to your virtualenv:
.venv/bin/pip install 'slack-sdk>=3.27'
```

> **Security note:** Your bot and app tokens grant access to your Slack workspace. Never commit them to a public repo. If you accidentally leak them, rotate them immediately in the Slack app settings.

## Step 8: Configure Environment

Edit your `.env` file:

```bash
# Messaging provider
KOAN_MESSAGING_PROVIDER=slack

# Slack credentials (all required)
KOAN_SLACK_BOT_TOKEN=xoxb-your-bot-token
KOAN_SLACK_APP_TOKEN=xapp-your-app-token
KOAN_SLACK_CHANNEL_ID=C01234ABCD
```

Or in `instance/config.yaml`:

```yaml
messaging:
  provider: "slack"
```

## Step 9: Start Kōan

```bash
make start
```

You should see in the logs:
```
[init] Messaging provider: SLACK, Channel: C01234ABCD
[slack] Socket Mode connected.
```

## Troubleshooting

### "Auth test failed"

- Verify your `KOAN_SLACK_BOT_TOKEN` starts with `xoxb-`
- Make sure the app is installed to the workspace (Step 4)
- Check that scopes are correct (Step 3)

### "Socket Mode connection failed"

- Verify your `KOAN_SLACK_APP_TOKEN` starts with `xapp-`
- Make sure Socket Mode is enabled (Step 2)
- The `connections:write` scope must be on the App-Level Token

### Bot not receiving messages

- Make sure the bot is invited to the channel (`/invite @koan`)
- Verify the `KOAN_SLACK_CHANNEL_ID` matches the channel
- Check that event subscriptions are enabled (Step 3)
- Messages from other bots and message subtypes (edits, joins) are filtered out

### Messages not delivered or rate limiting

- Slack limits `chat.postMessage` to ~1 message/second. Kōan handles this automatically with built-in rate limiting.
- Long messages are chunked to 4000 characters per message.

## Architecture Notes

- **Socket Mode**: Kōan uses Slack's Socket Mode (WebSocket) for receiving events — no public URL or ngrok needed
- **Event buffering**: Incoming messages are buffered in a thread-safe queue and processed on each poll cycle
- **Single channel**: Kōan only listens and responds in the configured channel (ignores DMs and other channels)
- **@mention stripping**: When you @mention the bot, the mention prefix is automatically removed before processing
