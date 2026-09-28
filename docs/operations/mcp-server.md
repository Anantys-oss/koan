---
type: doc
title: "MCP Server"
description: "Configure Kōan's MCP server for local stdio clients or remote Streamable HTTP clients: skill discovery, command schemas, shared bearer auth, TLS proxying, audits, and lifecycle."
tags: [operations]
created: 2026-09-09
updated: 2026-09-15
---

# MCP Server

Kōan can expose a curated part of its REST API as MCP tools. Two transports are
available. By default MCP clients launch the server as a subprocess over
**stdio** (no new port). Opt-in **Streamable HTTP** serves the same tools over
`/mcp` on a loopback listener for remote clients. Either way tool calls go
through the existing REST API and appear in `logs/api.log`; HTTP requests are
additionally audited in `logs/mcp.log`.

Both layers default off. Enable the REST API, configure its bearer token, and
then enable MCP:

```yaml
api:
  enabled: true
  host: "127.0.0.1"
  port: 8420

mcp:
  enabled: true
  tools_allow_destructive: false
```

MCP derives its URL from `api.host` and `api.port`. It gets the token from
`KOAN_API_TOKEN`, falling back to `api.token`; no MCP-specific secret exists.
Because the client spawns the server itself rather than going through `make`,
the entrypoint loads `$KOAN_ROOT/.env` at startup, so a token kept there (the
documented preference) reaches it without being repeated in the client's `env`.
With `mcp.enabled: false` or no `mcp` mapping, the subprocess refuses to run and
prints the setting needed to enable it.

## Install and configure

```bash
make api-token    # generate the shared bearer token
make api          # run the required REST API
make mcp-setup    # install optional SDK dependencies
make mcp-config   # print JSON for your MCP client configuration
```

Paste the printed `mcpServers.koan` block into Claude Code, Claude Desktop, or
another stdio-capable client. `make mcp-config` only prints JSON; it never edits
client configuration. Paths are absolute so clients can launch Kōan outside
the checkout's current directory.

Use `make mcp` to run the server in a terminal while debugging. MCP clients
normally launch `bin/koan-mcp` using the checkout virtual environment, as shown
by `make mcp-config`.

## Streamable HTTP

HTTP mode is opt-in:

```yaml
api:
  enabled: true
  host: "127.0.0.1"
  port: 8420

mcp:
  enabled: true
  transport: "http"
  host: "127.0.0.1"
  port: 8421
  tools_allow_destructive: false
```

Install the optional runtime once with `make mcp-setup`, then `make start`.
The endpoint is `http://127.0.0.1:8421/mcp`.

`make start` is enough only when Kōan runs under **its own process manager**
(`pid_manager`, the default). There, `mcp` is in `PROCESS_NAMES`, `start_mcp()`
is gated on `mcp.enabled` plus `transport: http`, and `make stop`, `make status`
and `make logs` all know about it. On a **systemd host the config flag alone
does nothing** — see [systemd hosts need their own unit](#systemd-hosts-need-their-own-unit).

Every HTTP request requires the same token as the REST API:

```http
Authorization: Bearer <KOAN_API_TOKEN>
```

Missing or empty credentials return 401; incorrect credentials return 403.
HTTP startup refuses to listen when no API token is configured. Request audits
appear in `logs/mcp.log` without headers, bodies, query strings, or tokens.
Audit fields are escaped to one printable token each, so a crafted request path
cannot inject extra lines.

`mcp.transport` accepts `stdio` or `http`. An unrecognised value never opens a
listener, and `make start` refuses to start the MCP daemon, prints the offending
value, and exits non-zero rather than reporting a listener that was never bound.
A configuration that cannot be read at all is reported the same way, and `mcp`
stays listed in `make status`, so an already-running daemon is never hidden
behind a transient read failure.

An address that cannot be bound — `mcp.port` already taken, `mcp.host` not
assigned to this machine — fails the same way rather than later and quietly. The
daemon binds its listener before it records its PID, and `make start` reads that
PID as proof of a successful launch, so the bind error is named on the console
and `make start` exits non-zero instead of reporting `mcp` as started.

Everything after that PID — the MCP SDK import, tool registration, building the
transport — can still fail, and a PID alone would report those as a healthy
boot. So the daemon writes a separate readiness marker (`.koan-ready-mcp`) at
the moment it is about to accept requests, and `make start` waits for it, up to
20 seconds past the PID. A daemon that exits, or never gets that far, is
reported as an MCP start failure pointing at `logs/mcp.log`, and `make start`
exits non-zero. The marker is removed when the daemon exits, and cleared before
each launch, so a marker left by a `kill -9` is never read as this run's
readiness.

A request that reaches the MCP app is audited twice: once on arrival, with `-`
in the status column, and once when its response starts, with the status. A
request the server refuses itself — 401, 403 — is audited once. The arrival
entry is what lets the server refuse a request it cannot record, rather than
running it first and refusing the next one.

If `logs/mcp.log` becomes unwritable — a full disk, or a rotation step that
changes its owner — the server stops serving instead of serving unaudited
requests, starting with the request that detects the failure. Clients then get
`503 audit_unavailable`. The reason goes to syslog
(tag `koan-mcp`) and to `logs/api.log`, because the daemon's own stderr goes to
the file that just failed, and `logs/api.log` shares its volume. Service resumes
on the first request after the file is writable again.

`make mcp-config` prints a transport-appropriate client block. In HTTP mode
the JSON contains the bearer token, so do not paste it into a tracked file or
attach it to an issue.

## Connecting a client

Once the server is running, pointing a client at it is two commands. Nothing
below needs a Kōan checkout, a Python environment, or an SSH tunnel on the
client machine — only network reach to the bind address.

Read the token off the host rather than copying it around. It is the same
`KOAN_API_TOKEN` the REST API uses:

```bash
TOKEN=$(ssh <bot-host> 'sed -n "s/^KOAN_API_TOKEN=//p" /path/to/koan/.env')
```

### Claude Code

```bash
claude mcp add --scope user --transport http <server-name> http://<bot-host>:8421/mcp \
  --header "Authorization: Bearer $TOKEN"
```

`<server-name>` is yours to choose. It labels the connection and prefixes the
tool names in the client, so a machine talking to two Kōan hosts can register
both without collision — `my_bot` and `staging_bot` rather than `koan` twice.
`--scope user` registers it for every project; drop it to scope the server to
the current directory.

Restart the client — MCP servers are only read at startup — then confirm:

```console
$ claude mcp list
<server-name>: http://<bot-host>:8421/mcp (HTTP) - ✔ Connected
```

`serverInfo.name` is always `koan`, because it names the product rather than
the deployment. To tell two hosts apart, call `koan_projects_list` and compare
the repositories each one manages.

### Any other MCP client

`make mcp-config` prints a ready-made block for the configured transport. In
HTTP mode it contains the bearer token, so redirect it to the client's config
rather than pasting it into a ticket or a tracked file. The shape is:

```json
{
  "mcpServers": {
    "<server-name>": {
      "type": "http",
      "url": "http://<bot-host>:8421/mcp",
      "headers": { "Authorization": "Bearer <KOAN_API_TOKEN>" }
    }
  }
}
```

### Before handing the endpoint to someone else

The bearer token is a full-privilege credential, not a read key. Say so
explicitly when sharing an endpoint:

- **It reaches administrative routes.** The same token authenticates
  `/v1/shutdown`, `/v1/restart` and `/v1/config` on the REST API.
- **`exec_operation` is not fenced off.** It can invoke any `operation_id` in
  `koan/openapi.yaml`, including those routes. It carries a destructive hint so
  clients prompt first, but that is a client-side courtesy, not a server-side
  restriction.
- **A client config file holds the token in cleartext.** Claude Code writes it
  to `~/.claude.json`; other clients vary. Treat that file as a secret.
- **Plain HTTP puts the token on the wire in the clear.** Acceptable on a
  trusted network; beyond one, put it behind the TLS proxy below.

Rotating means changing `KOAN_API_TOKEN` on the host, restarting the REST API
and the MCP daemon, **and** updating every client config that carries the old
value — there is no server-side revocation.

## systemd hosts need their own unit

`KOAN_SERVICE_MANAGER=systemd` (or `systemd-user`) bypasses Kōan's process
manager entirely: the units invoke entrypoints directly, so `PROCESS_NAMES` and
`start_mcp()` are never consulted. Both installers — `koan/systemd/install-service.sh` for system scope and
`koan/systemd/install-user-service.sh` for `systemd --user` — install
**two** units, `koan.service` and `koan-awake.service`, and neither the REST API
nor MCP is among them. Setting `mcp.transport: http` on such a host is silently
inert: nothing listens on 8421 and nothing warns you.

| Unit | `ExecStart` | Installed by |
|---|---|---|
| `koan-awake.service` | `app/awake.py` | the installer |
| `koan.service` | `app/run.py` | the installer |
| `koan-api.service` | `app/api/server.py` | **write it yourself** |
| `koan-mcp.service` | `-m app.mcp` | **write it yourself** |

Everything below shows the **system-scope** form first. `systemd --user` hosts
need the same two hand-written units (`koan-api.service`, `koan-mcp.service`)
with three differences — see
[`systemd --user` hosts](#systemd---user-hosts) at the end of this section.

Copy `koan.service`'s `User=`, `Group=`, `Environment=` and `EnvironmentFile=`
lines verbatim, so the unit runs as the same account and inherits `KOAN_ROOT`,
`PYTHONPATH`, the pinned `PATH` and `SSH_AUTH_SOCK`.

`User=`/`Group=` are **not optional on a system-scope unit**. Omit them and
systemd runs MCP as root while the agent loop runs as the installer's
unprivileged user — a needlessly wide privilege boundary for a network listener,
and a source of root-owned PID, log and state files inside an otherwise
user-owned installation. `install-service.sh` sets both on `koan.service` and
`koan-awake.service`; the API and MCP units must match. Read the values off the
installed unit rather than guessing:

```bash
systemctl cat koan.service | grep -E '^(User|Group)='
```

Leave both out of a `systemd --user` unit: the only value systemd accepts there
is the account the user manager already runs as, so they buy you nothing.

```ini
# /etc/systemd/system/koan-mcp.service — system scope
[Unit]
Description=Kōan MCP HTTP
After=network.target koan.service koan-api.service
PartOf=koan.service

[Service]
Type=simple
User=<same as koan.service>
Group=<same as koan.service>
WorkingDirectory=/path/to/koan/koan
EnvironmentFile=/path/to/koan/.env
Environment=KOAN_ROOT=/path/to/koan
Environment=PYTHONPATH=/path/to/koan/koan
Environment=PATH=<same as koan.service>
Environment=SSH_AUTH_SOCK=/path/to/koan/.ssh-agent-sock
ExecStart=/path/to/koan/.venv/bin/python -m app.mcp
Restart=on-failure
RestartSec=10
StandardOutput=append:/path/to/koan/logs/mcp-stdout.log
StandardError=append:/path/to/koan/logs/mcp-stdout.log

[Install]
WantedBy=multi-user.target
```

### `ExecStart` must be `-m app.mcp`, not the script path

Python puts a **script's own directory** at `sys.path[0]`, ahead of
`PYTHONPATH` and the stdlib. Launching the entrypoint by path therefore puts
`koan/app/mcp/` first, where any module named after a stdlib top-level package
shadows it for the whole process — the failure mode that an earlier
`app/mcp/http.py` produced, breaking Starlette's `import http.cookies`:

```text
File ".../starlette/responses.py", line 4, in <module>
    import http.cookies
ModuleNotFoundError: No module named 'http.cookies'; 'http' is not a package
```

Run it as a module instead. Then `sys.path[0]` is the working directory
(`koan/`), `http` resolves to the stdlib, and `app.mcp.*` still resolves inside
the package. Kōan's own process manager uses that form (`start_mcp()` spawns
`python -m app.mcp`), so a hand-written unit should match it. The transport
module is also named `http_transport.py` rather than `http.py`, which keeps the
script form working today — but naming discipline is one rename away from
lapsing, and the module launch is not.

### `PartOf=` alone will lose the daemon

`PartOf=` propagates **stop and restart, never start**. So when `koan.service`
crashes and systemd restarts it, `koan-api` and `koan-mcp` are stopped with it
and nothing brings them back — the daemon returns healthy while its API and MCP
listeners stay dead. Declare them on the parent:

```ini
# koan.service, [Unit]
Requires=koan-awake.service
Wants=koan-api.service
Wants=koan-mcp.service
BindsTo=koan-awake.service
```

`Wants=`, not `Requires=` — a broken API or MCP listener must never stop the
agent loop from starting.

Put those lines in a **drop-in**, not in `koan.service` itself — both installers
rewrite that file wholesale from their template, so an edit in place vanishes on
the next `make install-service` / `make install-user-service` and the daemons go
back to never restarting:

```bash
# system scope
sudo install -d /etc/systemd/system/koan.service.d
# systemd --user
install -d ~/.config/systemd/user/koan.service.d
```

Name the file `10-koan-extras.conf` and give it just the `[Unit]` header and the
two `Wants=` lines. Drop-ins are merged on top of the unit and are left alone by
both installers.

### Enable it — system scope

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now koan-api koan-mcp
systemctl list-dependencies koan.service | head -6
ss -lntp | grep 8421
```

### `systemd --user` hosts

`install-user-service.sh` writes to `~/.config/systemd/user`, so the plain
`systemctl` above cannot see these units at all — every call needs `--user`.
Three differences from the system unit:

- **Drop `User=` and `Group=`.** The user manager already runs as you.
- **`WantedBy=default.target`, not `multi-user.target`.** `multi-user.target`
  is a system target; a user unit wanting it never gets pulled in.
- **Unit path is `~/.config/systemd/user/`,** not `/etc/systemd/system/`. No
  `sudo` anywhere.

```bash
systemctl --user daemon-reload
systemctl --user enable --now koan-api koan-mcp
systemctl --user list-dependencies koan.service | head -6
ss -lntp | grep 8421
```

Run those from a real login session. From `sudo -niu <user>` the user bus is
not wired up, so export it first — the same prefix the Makefile uses:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
```

Lingering must be on (`loginctl show-user "$(id -un)"` → `Linger=yes`) or
`/run/user/<uid>` disappears with your session and the daemons stop with it.
See [Running as a systemd --user Service](../setup/systemd-user.md).

## TLS and reverse proxy

Kōan does not terminate TLS or apply public-network rate limits. Keep
`mcp.host` on loopback and expose it through a hardened reverse proxy:

```nginx
server {
    listen 443 ssl;
    server_name koan.example.com;

    ssl_certificate     /etc/ssl/certs/koan.pem;
    ssl_certificate_key /etc/ssl/private/koan.key;

    location /mcp {
        proxy_pass http://127.0.0.1:8421;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_set_header Host 127.0.0.1:8421;
        proxy_set_header X-Forwarded-For $remote_addr;
        limit_req zone=koan_api burst=20 nodelay;
    }
}
```

Remote clients connect to `https://koan.example.com/mcp` and send the bearer
header on every request. Nginx forwards `Authorization` by default. The
loopback `Host` override preserves the MCP SDK's DNS-rebinding protection.

Binding MCP directly to a non-loopback address emits a warning. It does not
add TLS, rate limiting, or firewall rules.

Rotate `KOAN_API_TOKEN` as one credential for both hops, then restart the REST
API and MCP daemon so the outbound MCP REST client uses the new value.

## Reaching a remote host without TLS: stdio over SSH

MCP framing is line-delimited JSON-RPC over stdio, so it survives an SSH pipe
unchanged. That gives a third option between "loopback only" and "terminate TLS
in nginx", and it is the right one for a trusted-network or development host:
**the bearer token never leaves the machine running Kōan.**

```bash
claude mcp add --transport stdio koan -- \
  ssh -T user@host \
  'cd /path/to/koan && KOAN_ROOT=/path/to/koan exec .venv/bin/python bin/koan-mcp'
```

Three things this depends on:

- **`ssh -T`.** A pseudo-terminal would corrupt the JSON-RPC framing.
- **Setting `KOAN_ROOT` explicitly.** `app.utils` refuses to boot when that
  variable is missing. Without it `bin/koan-mcp` dies on import with
  `KOAN_ROOT environment variable is not set`, so the client reports a
  connected-but-dead server. Nothing else needs exporting: a non-interactive
  SSH command inherits none of the unit's environment, but the entrypoint
  reads `$KOAN_ROOT/.env` itself before it resolves the token.
- **Keeping the remote command as one argv element.** Splitting it fails in a
  confusing place, well after the client reports the server as connected.

Probing it by hand needs `initialize`, `notifications/initialized` and
`tools/list` written in that order, from something that holds stdin open until
the reply arrives — a bare `printf | ssh` closes stdin first and returns only
the `initialize` reply, which looks like a broken server.

## Optional dependency

Core Kōan does not install the MCP SDK because MCP defaults off and the SDK adds
a substantial async/web and validation stack. `koan/requirements-mcp.txt`
currently installs MCP SDK 2.x and its direct dependency families, including
Pydantic, AnyIO, HTTPX 2/HTTPCore 2, Starlette, Uvicorn, JSON Schema tooling,
multipart/SSE support, OpenTelemetry API, JWT, and cryptography. Keeping that
stack separate preserves the lean main requirements and Python test matrix.

Running `bin/koan-mcp` without the SDK prints `run make mcp-setup` and exits
without a traceback.

## Tool discovery

Kōan defines sixteen curated `koan_*` tools plus generic `exec_operation`.
Fifteen curated tools appear by default; `koan_missions_delete` requires its
separate destructive-tool gate. Every published tool has a picker title,
detailed description, documented parameters, and explicit MCP behavior hints.

| Tool | REST operation | Annotation |
|---|---|---|
| `koan_skills_list` | `GET /v1/skills` | read-only |

At connection time, server instructions direct clients to begin with
`koan_status`, follow mission state with `koan_missions_get`, and retrieve a
completed structured result with `koan_missions_result`. Mission-list calls
serve queue browsing, not result polling.

Curated parameter descriptions and enforceable numeric, pattern, enum, or
choice constraints come from the committed OpenAPI document. Route docstring
bodies provide REST detail, while `x-koan-mcp-description` carries
agent-specific call-order and safety advice.

Prefer `koan_missions_create.command` for work represented in its command
choices. Those names come from API-exposed skill frontmatter. Call
`koan_skills_list` for command usage, aliases, and flags; use `text` only when
no exposed skill covers the work.

`exec_operation` is published alongside them, so a default client sees **16
entries** in `tools/list`, not fifteen.

`koan_missions_delete` is the one further curated tool, and appears only when:

```yaml
mcp:
  enabled: true
  tools_allow_destructive: true
```

It carries a destructive hint, and brings `tools/list` to 17 entries. Shutdown,
restart, updates, and all project create/update/delete operations never appear
as named tools.

`exec_operation` is a broad escape hatch, but not a hole in that gate. It
accepts an OpenAPI `operation_id`, `path`, `query`, and optional JSON `body`,
and carries destructive and open-world hints. `tools_allow_destructive` governs
its reach as well:

- **Default (`false`).** It reaches any operation in `koan/openapi.yaml`
  *except* shutdown, restart, update, release update, project
  create/update/delete, **and every destructive route** — anything `DELETE` or
  marked `x-koan-destructive`, such as mission delete, i.e. exactly what the
  flag hides from `tools/list`. Those are refused locally — the request never
  leaves the process — with an error naming the flag that would permit them.
- **`true`.** It reaches every documented operation, including those.

So a default deployment cannot be talked into stopping the agent, dropping a
watched project, or deleting a mission through either surface, and one flag
moves both. A new route marked `x-koan-destructive` is closed on both surfaces
the moment it is marked — the escape hatch reads the marker, it does not carry
a second list to keep in sync.

Named exposure fails closed. A REST route must carry its explicit MCP marker
and appear in Kōan's fixed curation table. Adding a route to OpenAPI alone never
publishes a model-facing tool.

## API downtime

An unreachable REST API does not prevent MCP startup. The server logs a warning
to stderr and remains available, allowing recovery without restarting the MCP
client. Failed tool calls include the attempted URL and these repairs:

1. Set `api.enabled: true` in `instance/config.yaml`.
2. Run `make api-token` and configure its token.
3. Start the API with `make api`.

## Provider MCP configuration compatibility

Kōan can also load third-party MCP servers into its own CLI provider roles.
Those client config paths now use `mcp.configs` when the mapping above exists:

```yaml
mcp:
  enabled: true
  tools_allow_destructive: false
  configs:
    - "/path/to/mcp-config.json"
```

Legacy top-level list syntax remains accepted for provider configuration, but
cannot also contain server settings. Per-project `mcp` lists in `projects.yaml`
remain unchanged.

### Automatic migration of the legacy list

An `instance/config.yaml` written before the MCP server landed holds `mcp` as a
bare list:

```yaml
mcp:
  - "/path/to/mcp-config.json"
```

On the first startup after upgrading, Kōan rewrites that block in place to the
mapping form (`mcp.configs`) so server settings can be added by hand later. The
rewrite is:

- **comment-preserving** — only the `mcp:` block's lines change; the rest of the
  file, including every comment, is untouched;
- **verified** — the result is re-parsed and compared before it is written, so a
  rewrite that would change any other key is discarded;
- **backed up** — the pre-migration file is copied to
  `instance/config.yaml.bak-mcp-mapping` once;
- **idempotent** — a config already in mapping form is left alone.

The migration is a convenience, not a requirement: the list form stays valid, so
a read-only or hand-reverted config still starts. Look for
`[migration] mcp: converted legacy list to 'mcp.configs' mapping` in the startup
log.

## See also

- [REST API](rest-api.md) — required HTTP layer, authentication, and audit log
- [Claude provider](../providers/claude.md) — loading third-party MCP servers into Kōan roles
- [MCP component contract](../../specs/components/mcp.md) — curation and safety invariants
