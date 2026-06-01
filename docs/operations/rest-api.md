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
| `GET` | `/v1/status` | yes | Agent state, mode, mission counts, pause info |

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
    "pause": {}
  },
  "missions": {
    "pending": 3,
    "in_progress": 1,
    "done": 42,
    "failed": 0
  }
}
```

### Missions

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/missions` | yes | List API-queued missions. Query params: `?status=pending\|in_progress\|done\|failed\|removed`, `?project=name` |
| `POST` | `/v1/missions` | yes | Queue a new mission |
| `GET` | `/v1/missions/{id}` | yes | Get mission by id (reconciles vs missions.md) |
| `DELETE` | `/v1/missions/{id}` | yes | Cancel a pending mission (409 if already started) |

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
| `POST` | `/v1/restart` | yes | Write `.koan-restart` signal (picked up by run loop) |
| `POST` | `/v1/shutdown` | yes | Write `.koan-stop` signal |
| `POST` | `/v1/update` | yes | Pull upstream + signal restart |

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
