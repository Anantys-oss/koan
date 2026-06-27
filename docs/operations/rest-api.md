# REST API

Kōan exposes an **optional HTTP control layer** so external tools can queue missions, poll status, and manage the agent programmatically — in addition to the Telegram / Matrix / Slack messaging bridge.

The API is **disabled by default** and requires an explicit bearer token before it will serve any requests (fail-closed).

---

## Enable & configure

In `instance/config.yaml`:

```yaml
api:
  enabled: true       # Include API in managed processes (default: false)
  host: "127.0.0.1"  # Bind address (default: 127.0.0.1 — loopback only)
  port: 8420          # HTTP port (default: 8420)
  threads: 8          # waitress worker threads (default: 8)
  # token: ""         # Bearer token fallback (prefer KOAN_API_TOKEN env var)
```

Generate a random token and configure it:

```bash
make api-token    # prints a random token + setup instructions
```

Or set manually in `.env` (preferred — keeps secrets out of the config tree):

```bash
KOAN_API_TOKEN=your-secret-token
```

Alternatively set `api.token` in `config.yaml`, but environment variable takes precedence.

---

## Start/stop

```bash
make api          # standalone foreground server
make start        # includes API when api.enabled: true
make stop         # stops all managed processes including API
make status       # shows API PID when running
```

---

## Authentication

Every endpoint except `GET /v1/health` requires:

```
Authorization: Bearer <your-token>
```

| Response | Condition |
|---|---|
| `401` | `Authorization` header missing or malformed |
| `403` | Token present but incorrect |

Token comparison uses `hmac.compare_digest` to prevent timing attacks. If no token is configured, **all authenticated requests return 403** — the server never accepts unauthenticated control requests.

---

## Endpoint reference

All responses are JSON. Errors use a uniform envelope:
```json
{"error": {"code": "...", "message": "..."}}
```

### Health

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/health` | none | Liveness probe — always returns `{"status":"ok","name":"koan","version":"..."}` |

### Status

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/status` | yes | Agent state, mode, mission counts, signal flags, attention count, live execution truth |

Response:
```json
{
  "agent": {
    "state": "working|sleeping|paused|stopped|idle|contemplating|error_recovery",
    "mode": "REVIEW|IMPLEMENT|DEEP|null",
    "run_info": "12/20",
    "project": "my-project",
    "focus": false,
    "status_text": "Run 12/20 — executing",
    "pause": {},
    "elapsed_seconds": 42,
    "execution": {
      "state": "idle|working|stalled|zombie",
      "pid": 12345,
      "project": "my-project",
      "run_num": 12,
      "elapsed": 42,
      "last_output_age": 3.1,
      "sessions": 0
    }
  },
  "missions": {
    "pending": 3,
    "in_progress": 1,
    "done": 42,
    "failed": 0
  },
  "signals": {
    "stop_requested": false,
    "quota_paused": false,
    "paused": false
  },
  "attention_count": 2,
  "execution": {
    "provider_state": "idle|working|stalled|zombie",
    "in_progress_lines": 1,
    "zombie": false
  }
}
```

The `execution` block reports **observed** runtime state — backed by the live
provider PID in `.koan-active` and provider-output recency — not the
declarative `missions.md` ▶ timestamp, which can silently diverge (#2086):

- `working` — a live provider PID with recent (or not-yet-produced) output, or
  a live parallel worktree session (tracked in `sessions.json`).
- `stalled` — a live PID but no output for over 120s (hung session). A recorded
  stdout file that has vanished is treated as stalled, never as `working`.
- `zombie` — a recorded PID that is no longer alive.
- `idle` — no provider running.

The top-level `execution.zombie` is `true` when an *In Progress* mission line
exists but no live provider process backs it. To avoid flapping during the
brief start/stop windows where the `missions.md` line and the `.koan-active`
signal momentarily disagree, the orphan check requires that the run-loop
heartbeat (`.koan-run-heartbeat`) has gone stale before flagging the `idle`
case — a recorded-but-dead PID is always flagged immediately. Live parallel
sessions also suppress the flag. The same cross-check backs the `make status`
`execution:` line.

### Missions

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/missions` | yes | List API-queued missions. Query params: `?status=pending\|in_progress\|done\|failed\|removed`, `?project=name` |
| `POST` | `/v1/missions` | yes | Queue a new mission |
| `GET` | `/v1/missions/{id}` | yes | Get mission by id (reconciles vs missions.md) |
| `PATCH` | `/v1/missions/{id}` | yes | Edit a pending mission's text (409 if not pending) |
| `DELETE` | `/v1/missions/{id}` | yes | Cancel a pending mission (409 if already started) |
| `POST` | `/v1/missions/reorder` | yes | Reorder a pending mission in the queue |

**POST /v1/missions** body:
```json
{
  "command": "/fix https://github.com/org/repo/issues/42",
  "project": "my-project",
  "urgent": false
}
```
Use `command` for slash commands or `text` for free-form missions. `project` adds a `[project:name]` tag. `urgent` inserts at the top of the queue.

Response (202):
```json
{"id": "uuid", "status": "pending"}
```

**GET /v1/missions/{id}** response:
```json
{
  "id": "uuid",
  "text": "- [project:koan] /fix ...",
  "project": "koan",
  "status": "pending|in_progress|done|failed|removed",
  "created": 1748700000.0,
  "result_line": "✅ (2026-05-31 14:22) Fixed the bug"
}
```

Mission status is reconciled on each read against the live `missions.md` state.

**PATCH /v1/missions/{id}** body:
```json
{"text": "Updated mission description"}
```

Response (200):
```json
{"id": "uuid", "status": "pending"}
```

Returns 409 if the mission is not in `pending` status. Returns 422 if `text` is missing or empty.

**POST /v1/missions/reorder** body:
```json
{"mission_id": "uuid", "target_position": 1}
```

Response (200):
```json
{"id": "uuid", "status": "pending"}
```

`target_position` is 1-indexed within the pending queue. Returns 409 if the mission is not pending. Returns 422 if the target position is out of range.

### Projects

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/projects` | yes | List known projects |
| `POST` | `/v1/projects` | yes | Add a project (runs `add_project` skill) |
| `DELETE` | `/v1/projects/{name}` | yes | Remove a project (runs `delete_project` skill) |

**POST /v1/projects** body:
```json
{"github_url": "https://github.com/org/repo", "name": "optional-name"}
```

### Pause / resume

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/pause` | yes | Pause the agent |
| `POST` | `/v1/resume` | yes | Resume the agent |

**POST /v1/pause** body (optional):
```json
{"duration": "2h"}
```
Duration formats: `2h`, `30m`, `1h30m`. Omit for indefinite pause.

### Config

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/config` | yes | Effective config + project list. Secrets masked. |

Secret fields (keys containing `token`, `password`, `secret`, `api_key`) are replaced with `"***"`.

### Admin

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/restart` | yes | Signal restart via per-consumer markers `.koan-restart-run` + `.koan-restart-bridge` (picked up by run loop and bridge) |
| `POST` | `/v1/shutdown` | yes | Write `.koan-stop` signal |
| `POST` | `/v1/update` | yes | Pull latest commit on main + signal restart |
| `POST` | `/v1/update_release` | yes | Checkout most recent release tag + signal restart |

### Observability

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/usage` | yes | Token usage payload (mirrors the dashboard `/api/usage`) |
| `GET` | `/v1/metrics` | yes | Mission metrics, global or per-project |
| `GET` | `/v1/logs` | yes | Recent `run.log` / `awake.log` lines |

**GET /v1/usage** query params:

| Param | Default | Description |
|---|---|---|
| `days` | `7` | Window length, clamped to `[1, 100]` |
| `offset` | `0` | Shift the window back by this many `granularity` units |
| `granularity` | `day` | `day`, `week`, or `month` bucketing |
| `stacked` | `false` | Include per-project breakdown in the series |
| `project` | _(all)_ | Restrict totals/series to a single project |

**GET /v1/metrics** query params:

| Param | Default | Description |
|---|---|---|
| `days` | `30` | Lookback window, clamped to `[0, 365]` |
| `project` | _(all)_ | Per-project metrics + trend; omit for global metrics with per-project trends |

**GET /v1/logs** query params:

| Param | Default | Description |
|---|---|---|
| `source` | `all` | `run`, `awake`, or `all` |
| `limit` | `200` | Max lines per source, clamped to `[1, 2000]` |
| `q` | _(none)_ | Case-insensitive substring filter |

Response:
```json
{"lines": [{"n": 1, "text": "...", "source": "run"}], "total": 1}
```

Malformed integer params (`days`, `offset`, `limit`) return `422` with an `invalid_request` error rather than silently substituting the default.

---

## Security

- **Loopback-only by default** — `host: "127.0.0.1"` prevents external access without explicit configuration change.
- A warning is logged at startup when bound to a non-loopback address.
- **TLS and rate limiting** are delegated to a reverse proxy (nginx, Caddy). The API has no built-in TLS.
- Per-request audit log: `logs/api.log` — `YYYY-MM-DDTHH:MM:SS <ip> METHOD /path STATUS`.
- Token is never logged.

### Reverse proxy example (nginx)

```nginx
server {
    listen 443 ssl;
    server_name koan.example.com;

    ssl_certificate     /etc/ssl/certs/koan.pem;
    ssl_certificate_key /etc/ssl/private/koan.key;

    location /v1/ {
        proxy_pass http://127.0.0.1:8420;
        proxy_set_header X-Forwarded-For $remote_addr;
        limit_req zone=koan_api burst=20 nodelay;
    }
}
```

---

## External multi-instance registration

Each Kōan instance has its own `KOAN_API_TOKEN`. An external operator (e.g. a CI system managing multiple instances) can:

1. Register an instance: `GET /v1/health` — 200 means the instance is reachable.
2. Queue work: `POST /v1/missions` — returns an `id` for polling.
3. Poll status: `GET /v1/missions/{id}` — status transitions `pending → in_progress → done/failed`.
4. Pause/resume on demand: `POST /v1/pause` / `POST /v1/resume`.

Each instance is fully independent; there is no shared coordination layer.

---

## Audit log

All authenticated requests are logged to `logs/api.log`:

```
2026-05-31T14:22:10 127.0.0.1 POST /v1/missions 202
2026-05-31T14:22:15 127.0.0.1 GET /v1/missions/abc123 200
```

Tokens are never written to the log.

---

## See also

- [`docs/operations/dashboard.md`](dashboard.md) — web dashboard (separate process, same config pattern)
- [`instance.example/config.yaml`](../../instance.example/config.yaml) — documented `api:` section
