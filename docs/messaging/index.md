# Messaging

* [Discord Messaging Provider](discord.md) - Setup guide for using Discord as Kōan's messaging bridge via REST polling instead of the Gateway/WebSocket API.
* [GitHub Notification-Driven Commands](github-commands.md) - Full reference for triggering Kōan via `@mention` commands in GitHub PR/issue comments, including config, dedup, security, and fallback scanning.
* [GitHub Webhooks — Push-Based Notification Triggering](github-webhooks.md) - Describes the opt-in push-based GitHub webhook receiver that collapses notification-polling latency while polling remains the reliability fallback.
* [Jira Integration](jira-integration.md) - Full reference for controlling Kōan via `@mention` commands in Jira issue comments, including project mapping, ADF parsing, and coexistence with GitHub.
* [Matrix Setup Guide](matrix.md) - Setup guide for using a Matrix homeserver as Kōan's messaging provider via the Client-Server HTTP API.
* [Messaging level (bridge verbosity)](messaging-level.md) - Explains the `messaging.level` setting (`normal`/`debug`) that controls how much lifecycle/progress chatter Kōan's Telegram/Slack bridge sends versus only logs.
* [Slack Setup Guide](slack.md) - Step-by-step guide to configuring Kōan with Slack (Socket Mode app setup, scopes, env vars) plus Slack-specific behavior like threading, reactions, and the assistant \"thinking\" status.
* [Telegram Setup Guide](telegram.md) - Step-by-step guide to configuring Kōan with Telegram (bot creation, chat ID, env vars), including group-chat privacy-mode setup and troubleshooting.
