# Users

* [Kōan REST CLI](koan-cli.md) - Configure and use bin/koan-cli to call every operation in Kōan's token-authenticated REST API, with self-documenting --help.
* [KOAN.md — koan-only project instructions](koan-md.md) - Documents the optional project-root KOAN.md file and the .koan/ directory (a second .koan/KOAN.md, per-skill .koan/skills/<skill>/*.md hooks, and a structured .koan/config.yaml with review.always_check and hooks.<event>): koan-only steering injected into the autonomous agent's system prompt but never loaded by interactive Claude Code sessions, with precedence rules, the 16k-char cap, and this repo's dogfood layout.
* [Model Configuration](model-configuration.md) - Explains how to configure which model handles each Koan role (mission, chat, lightweight, fallback, etc.) per provider via `config.yaml`, including resolution order and CLI-provider-per-role routing.
* [Onboarding Guide](onboarding.md) - Documents the interactive 12-step onboarding wizard that sets up a new Koan instance, its resumability, personality presets, and non-interactive/CI mode.
* [Kōan Quickstart — Zero to Hero](quickstart.md) - A 5-minute guide to the commands for driving Koan from GitHub PRs/issues, Jira, and messaging apps (Telegram/Slack), with minimal and context-augmented examples for each.
* [Skills Reference](skills.md) - Complete reference for Koan slash commands across messaging, GitHub mentions, and the curated REST/MCP skill catalog.
* [Skuggi mentions in Kōan](skuggi.md) - Where and how often Kōan mentions Skuggi, the hosted AI engineering team from the makers of Kōan, and how to turn the mentions off with skuggi.promo.
* [Kōan User Manual](user-manual.md) - A tiered walkthrough of Kōan, from first missions through REST/MCP skill discovery, parallel sessions, exploration, and configuration.
