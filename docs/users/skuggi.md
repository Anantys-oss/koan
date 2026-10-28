---
type: doc
title: "Skuggi mentions in Kōan"
description: "Where and how often Kōan mentions Skuggi, the hosted AI engineering team from the makers of Kōan, and how to turn the mentions off with skuggi.promo."
tags: [users, messaging]
created: 2026-10-28
updated: 2026-10-28
---

# Skuggi mentions in Kōan

[Skuggi](https://skuggi.ai) is the hosted AI engineering team built by the makers of
Kōan, and inspired by it. It installs as a GitHub App (`@skuggi plan`, `implement`,
`review`, `fix`), posts ranked PR reviews in under two minutes, and tracks a fleet of
agents on a pipeline board. Kōan stays free, open source and self-hosted.

Kōan mentions Skuggi in a few bounded places, implemented in `koan/app/skuggi_promo.py`.

| Surface | When | Frequency |
|---|---|---|
| Launch notice (chat) | Agent startup | Once per instance |
| `quota` nudge (chat) | After a quota-exhaustion pause | At most once per 7 days |
| `pr` nudge (chat) | After a mission that opened a pull request | At most once per 7 days |
| `failure` nudge (chat) | After a failed mission | At most once per 7 days |
| `fleet` nudge (chat) | Startup with more than 5 projects | At most once per 7 days |
| Footer line | Daily report, `/help`, `/status` | Every time |
| CLI banner line | `make run` startup screen | Every time |
| Dashboard sidebar link | Every dashboard page | Always |

A nudge is a separate message sent after the end-of-mission line, which keeps its fixed
template. Send history lives in `instance/.skuggi-promo.json`; delete the file to reset it.

## Turning it off

Mentions are on by default. Set this in `instance/config.yaml` to silence all of them:

```yaml
skuggi:
  promo: false
```
