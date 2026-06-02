# GitHub Webhooks — Push-Based Notification Triggering

By default, Kōan **polls** GitHub for @mentions on a throttled schedule (60s base,
backing off to 180s when idle). That delay is why the bot can feel slow to react
to a PR comment. This feature adds an **opt-in webhook receiver** so GitHub
*pushes* events to Kōan, collapsing the response latency from up to ~3 minutes
down to a few seconds.

## How it works

GitHub's REST notifications API has no push/streaming mechanism — **webhooks are
the only push transport GitHub offers.** The receiver is deliberately thin:

1. GitHub sends a webhook POST when a relevant event happens (comment, review,
   assignment, review request).
2. The receiver verifies the HMAC-SHA256 signature, filters to known repos and
   actionable event types.
3. On a match it writes the same `.koan-check-notifications` signal that the
   `/check_notifications` command uses.
4. The run loop consumes that signal within ~10s and performs an **immediate
   notification poll**, bypassing the backoff.

The webhook is a *latency trigger*, not a replacement for polling. It does **not**
parse @mentions, check permissions, or create missions itself — it reuses the
entire existing polling pipeline. Polling stays on as the reliability fallback:
if a webhook delivery is dropped or retried, the next poll still catches the
mention. **Webhook for latency, poll for reliability.**

```
GitHub event ──HTTP POST──▶ receiver ──writes .koan-check-notifications──▶ run loop
                              │                                              │
                       (verify signature,                            (forced poll within ~10s,
                        filter repo/event)                            full dedup + permissions)
```

## Requirements

- A **publicly reachable endpoint** for GitHub to POST to. Most self-hosted
  setups are behind NAT, so front the receiver with a tunnel:
  - [smee.io](https://smee.io) — zero-install, purpose-built for webhooks.
  - `cloudflared tunnel` — Cloudflare Tunnel.
  - `ngrok http 8474` — quick local tunnels.
- A **shared secret** so only GitHub can trigger polls.

The receiver binds to `127.0.0.1` by default — the tunnel runs on the same host
and forwards to localhost, so the receiver is never directly internet-exposed.

## Setup

### 1. Generate a secret

```bash
openssl rand -hex 32
```

Put it in your `.env` (it is **not** read from `config.yaml`):

```bash
KOAN_GITHUB_WEBHOOK_SECRET=<the-generated-secret>
```

### 2. Enable the receiver

In `instance/config.yaml`:

```yaml
github:
  nickname: "koan-bot"
  commands_enabled: true
  webhook:
    enabled: true        # start the receiver in the bridge process
    port: 8474           # default
    host: "127.0.0.1"    # default (loopback — front with a tunnel)
```

Restart the bridge (`make stop && make start`). You should see:

```
[init] GitHub webhook receiver started (push-based triggering)
```

If `enabled` is true but `KOAN_GITHUB_WEBHOOK_SECRET` is unset, the receiver
refuses to start (it never runs without signature verification) and logs a
warning — polling continues unaffected.

A `port` outside `1–65535` (or non-numeric) and a `host` that isn't a non-empty
string are rejected with a logged warning and fall back to the defaults — check
the startup logs if a configured value seems ignored.

### 3. Expose it with a tunnel

Example with smee.io:

```bash
# Get a channel URL from https://smee.io/new, then:
npx smee-client --url https://smee.io/YOUR_CHANNEL --target http://127.0.0.1:8474
```

### 4. Configure the webhook in GitHub

In the repo (or org) **Settings → Webhooks → Add webhook**:

- **Payload URL**: your tunnel URL (e.g. `https://smee.io/YOUR_CHANNEL`).
- **Content type**: `application/json` (required — the signature is computed
  over the raw JSON body).
- **Secret**: the same value as `KOAN_GITHUB_WEBHOOK_SECRET`.
- **Events**: "Let me select individual events" →
  *Issue comments*, *Pull request review comments*, *Pull request reviews*,
  *Issues*, *Pull requests*. (Or "Send me everything" — non-actionable events
  are ignored.)

GitHub sends a `ping` on save; the receiver replies `pong` (HTTP 200).

## Running standalone

Instead of embedding the receiver in the bridge, you can run it as its own
process (useful for debugging or separate supervision):

```bash
KOAN_GITHUB_WEBHOOK_SECRET=... make webhook
```

This honors the same `github.webhook.port` / `host` config.

## Which events trigger a poll

| GitHub event | Actions that trigger |
|---|---|
| `issue_comment` | `created` |
| `pull_request_review_comment` | `created` |
| `pull_request_review` | `submitted` |
| `commit_comment` | any |
| `issues` | `assigned` |
| `pull_request` | `assigned`, `review_requested` |

Everything else (pushes, labels, syncs, etc.) is ignored. Events from repos not
in your `projects.yaml` are authenticated but never trigger a poll.

## Security

- **Signature verification**: every request must carry a valid
  `X-Hub-Signature-256` HMAC over the raw body, compared in constant time. Bad
  or missing signatures get `401`.
- **No secret, no server**: the receiver will not start without
  `KOAN_GITHUB_WEBHOOK_SECRET`.
- **Loopback by default**: bind host is `127.0.0.1` unless you explicitly set
  `0.0.0.0`.
- **Body size cap**: payloads larger than 25 MB are rejected (`413`). A request
  whose body is shorter than its advertised `Content-Length` is rejected (`400`).
- **Per-request timeout**: each request is bounded to 5 seconds, so a slow or
  trickling client (Slowloris) cannot hold a worker thread open.
- **Signal debounce**: forced polls are coalesced to at most one every 5 seconds,
  so a burst of deliveries (GitHub retries, concurrent events) — or a replayed
  delivery from a secret holder — cannot turn the run loop into a tight loop of
  GitHub API calls. No event is lost: GitHub retries, and polling remains the
  reconciliation fallback.
- **Generic health probe**: `GET /` returns a generic `ok` and never fingerprints
  the service.
- The receiver only ever *triggers a poll*. It does not act on webhook payload
  contents directly, so a forged payload (without the secret) cannot create
  missions — and with the secret it can at most cause a redundant poll.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No "webhook receiver started" log | `webhook.enabled` not true, or secret unset. |
| GitHub shows `401` on deliveries | Secret mismatch between GitHub and `.env`, or content type isn't `application/json`. |
| Deliveries arrive but bot still slow | Repo not in `projects.yaml`, or the event/action isn't in the trigger table above. |
| `Address already in use` | Another process holds the port; change `webhook.port`. |

Even with everything misconfigured, polling keeps working — webhooks only make
it faster.
