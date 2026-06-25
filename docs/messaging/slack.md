# Slack Setup Guide

This guide covers setting up Kōan with Slack as the messaging provider. Slack uses Socket Mode for real-time bidirectional communication.

## Prerequisites

- A Slack workspace where you have permission to install apps (or can request admin approval)

## Fast path: create the app from a manifest

The quickest setup is to create the app from the ready-made manifest, which
pre-configures Socket Mode, scopes, and event subscriptions in one shot:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Select your workspace
3. Paste the contents of [`slack-app-manifest.json`](./slack-app-manifest.json) and create the app
4. Generate the tokens it needs (Steps 2 and 4 below) and skip the manual scope/event setup (Steps 3)

Then continue from Step 4 (Install to Workspace) to collect your tokens.

## Step 1: Create a Slack App (manual alternative)

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
   | `chat:write` | Send messages and set the "thinking" status |
   | `assistant:write` | Show the assistant "thinking" status (optional; `chat:write` also works) |
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
   - `assistant_thread_started` (optional — for the Assistant "thinking" status)
   - `assistant_thread_context_changed` (optional)

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
# Messaging provider. Recommended but no longer strictly required: if you set
# the KOAN_SLACK_* tokens below and no other provider, Kōan auto-detects Slack
# instead of defaulting to Telegram. Set this explicitly to remove any ambiguity.
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

The env var takes precedence over `config.yaml`. When neither names a provider,
Kōan resolves the one whose credentials are present — so configuring Slack alone
won't trigger a spurious "set telegram credentials" warning. Auto-detection is
deliberately conservative:

- If Telegram is already configured (`KOAN_TELEGRAM_TOKEN` + `KOAN_TELEGRAM_CHAT_ID`),
  Kōan never auto-switches away from it — selecting Slack would silently swap a
  working setup. Set `KOAN_MESSAGING_PROVIDER=slack` to switch intentionally.
- If credentials for more than one non-telegram provider are set, the choice is
  ambiguous and Kōan falls back to Telegram; set `KOAN_MESSAGING_PROVIDER` to
  disambiguate.

When auto-detection does pick a provider, it logs a line to the bridge stderr
(`auto-detected messaging provider 'slack' from credentials …`) so the
resolution is traceable.

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

### Bot connects but never replies

- **Confirm the provider is actually Slack.** Run `make logs` and look for
  `Messaging provider: SLACK`. If it says `TELEGRAM`, `KOAN_MESSAGING_PROVIDER=slack`
  is not set — setting only the `KOAN_SLACK_*` tokens is not enough.
- **You must @mention the bot.** Kōan ignores un-addressed channel chatter by
  design. Ping `@Koan ...` to start; after that you can keep replying in the
  thread without re-mentioning.

### Bot not receiving messages

- Make sure the bot is invited to the channel (`/invite @koan`)
- Verify the `KOAN_SLACK_CHANNEL_ID` matches the channel
- Check that event subscriptions are enabled (Step 3)
- Messages from other bots, the bot's own messages, and message subtypes (edits, joins) are filtered out

### Messages not delivered or rate limiting

- Slack limits `chat.postMessage` to ~1 message/second. Kōan handles this automatically with built-in rate limiting.
- Long messages are chunked to 4000 characters per message.

## How Kōan behaves in Slack

- **Mention to start**: In the configured channel, Kōan stays quiet until you
  @mention it (or it receives an `app_mention`). Ordinary channel chatter is
  ignored, so the bot is safe to drop into a shared channel.
- **Replies go in a thread**: When you @mention Kōan on a channel-root message,
  it replies in a **thread** under your message rather than cluttering the
  channel.
- **Threads continue without re-mentioning**: Once Kōan is engaged in a thread,
  you can keep replying in that thread without @mentioning it each time — it
  recognizes threads it is already participating in and keeps answering there.
- **De-duplication**: Slack delivers both an `app_mention` and a `message` event
  for the same channel mention; Kōan acts on it exactly once.
- **"Thinking" status**: While Kōan works on a chat reply, it shows a rotating
  status (greyed italic text under the bot name — "Thinking…", "Reading the
  code…", …) via Slack's assistant status API. It clears automatically when the
  reply posts. This is best-effort: if the API call fails it is silently skipped
  and never affects the reply itself. See "Thinking status" below.

## Thinking status

When you @mention Kōan, it shows a live status in the thread while it works,
then replaces it with the answer — the same idea as Slack's own assistant
"thinking" indicator.

- **How it works**: Kōan calls `assistant.threads.setStatus` with a rotating
  phrase, refreshed every few seconds, and clears it when the reply is sent.
- **Scope**: this uses the `chat:write` scope the app already has — no extra
  setup is strictly required. (`assistant:write` is also accepted and is
  included in the manifest for the richest experience.)
- **Where it renders**: Slack renders this status most reliably inside
  **Assistant threads**; on a plain channel @mention it may not show, depending
  on your workspace. Reinstalling with the updated manifest (which enables the
  `assistant:write` scope and the Assistant feature) gives it the best chance of
  rendering on channel threads. If it still doesn't appear, that's a Slack
  rendering limitation, not a failure — the reply is unaffected.
- **Scope note**: Kōan currently listens and replies **only in the configured
  channel** (`KOAN_SLACK_CHANNEL_ID`). It does not yet hold conversations in the
  Assistant pane / DMs, so enabling the Assistant feature affects status
  rendering only, not where Kōan responds.
- **Safe by design**: a failed status update is logged at most once to stderr
  and never blocks or alters the actual reply.

## Architecture Notes

- **Socket Mode**: Kōan uses Slack's Socket Mode (WebSocket) for receiving events — no public URL or ngrok needed
- **Event buffering**: Incoming messages are buffered in a thread-safe queue and processed on each poll cycle
- **Single channel**: Kōan only listens and responds in the configured channel (ignores DMs and other channels)
- **@mention stripping**: When you @mention the bot, the mention prefix is automatically removed before processing
- **Threading internals**: Slack threads are keyed by `thread_ts`. Kōan emits
  inbound events in the same Telegram-shaped envelope every provider uses, with
  an integer token as the `message_id`; that token maps back to the message's
  `thread_ts` so the bridge's existing reply-context plumbing routes replies into
  the right thread without any Slack-specific code in the main loop. Asynchronous
  agent notifications (mission updates from the outbox) have no reply context and
  post to the channel root.
