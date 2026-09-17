---
type: component-spec
title: "Component Spec — MCP Server"
description: "Defines Kōan's opt-in MCP front-end over stdio or Streamable HTTP, curated REST operation tools, destructive-tool gate, shared OpenAPI HTTP client boundary, and HTTP authentication/audit invariants."
tags: [web]
created: 2026-09-09
updated: 2026-09-15
---

# Component Spec — MCP Server

**Packages:** `koan/app/mcp/`, `koan/app/apiclient/`

## Purpose

Kōan offers an optional MCP server for LLM clients. It translates tool calls
into authenticated requests to Kōan's REST API. It never reads or mutates Kōan
runtime state directly: every call crosses the HTTP API and keeps its
authentication, validation, and `logs/api.log` audit trail.

## Architecture

```
MCP client ── stdio ──> app/mcp/ ──> app/apiclient/ ── HTTP ──> REST API
CLI user  ─────────────> app/cli/ ──> app/apiclient/ ── HTTP ──> REST API
                                      ↑
                              committed openapi.yaml
```

`app/apiclient/` owns OpenAPI loading, operation discovery, request planning,
and synchronous HTTP execution. `app/cli/` owns terminal parsing, profiles,
confirmation, output, and exit codes. `app/mcp/` owns curation, MCP schemas,
annotations, configuration gates, and transport lifecycle.

## Transports

`mcp.transport` accepts `stdio` or `http` and defaults to `stdio`.

- `stdio` is client-launched, creates no listener, and keeps its existing
  behavior.
- An unrecognised value resolves to `stdio`, so a typo can never open a
  listener — but it is never silent: with `mcp.enabled: true`, the process
  manager refuses to start the MCP daemon, names the offending value, and
  makes `make start` exit non-zero.
- `http` serves MCP Streamable HTTP at `/mcp`, binds `mcp.host` and
  `mcp.port`, and is managed as the `mcp` daemon. It binds its listener
  *before* claiming `.koan-pid-mcp` — the process manager reads that pidfile
  as proof of a successful start, so an address already in use must fail while
  no pidfile exists rather than becoming a reported start followed by a silent
  exit. Binding is two syscalls, so it does not consume the verify timeout the
  pidfile opens for the SDK import and spec parse that follow.
- The pidfile therefore proves the process exists, not that it serves. The
  daemon publishes a separate readiness marker (`.koan-ready-mcp`) from the
  point past which nothing in startup can still fail, and the process manager
  treats a start as successful only once that marker appears. A daemon that
  exits after claiming its pidfile — an incompatible SDK, a registration
  error, an SDK signature change inside the transport — is reported as a start
  failure naming `logs/mcp.log`, and `make start` exits non-zero. The marker is
  removed on exit and cleared before each launch, so one left by an
  uncatchable kill is never read as the next run's readiness.
- A `config.yaml` that cannot be read or parsed is reported as an MCP start
  failure, not downgraded to "not configured". Config getters resolve through a
  loader that degrades a broken file to an empty mapping, which would otherwise
  render `mcp.enabled` false and drop `mcp` from both `make start` and
  `make status` while a daemon still held the pidfile; the process manager
  probes the file itself before consulting any getter.
- Both transports construct tools only through `create_server()` and
  `build_tool_definitions()`.

HTTP requests require `Authorization: Bearer <api-token>`. Authentication
resolves the same secret as the REST API through `get_api_token()` and calls
`app.api.auth.check_token()`, which fails closed and compares tokens with
`hmac.compare_digest`.

HTTP request audits are written to `logs/mcp.log` as:

`YYYY-MM-DDTHH:MM:SS <peer-ip> METHOD /path STATUS`

Authorization headers, bearer tokens, request bodies, and query strings are
never written to the audit line. Each field is rendered as a single printable,
space-free token (non-printable characters replaced, spaces percent-encoded,
over-long values truncated), so an unauthenticated caller cannot forge audit
entries through a crafted request path.

A request forwarded to the MCP app is audited **before** it is forwarded, with
`-` as its status, and again when its response starts, with the status. A
request the middleware refuses itself is audited once, with its status. The
pre-dispatch entry is what makes the trail fail closed: auditing only the
response would let the request that *discovers* a broken sink complete its side
effect with no entry at all.

The audit trail fails closed. A failed audit write latches the middleware: that
request and every later one are refused with 503 `audit_unavailable` until the
sink accepts a write again. The latch is cleared only by a probe that **writes**
a marker line and forces it to disk — opening the file is not proof of
writability, because a full volume still accepts an append open. The warning
never goes to the audit sink itself (the launcher also redirects the daemon's
stderr into it). It goes
to syslog first — the only channel not on the volume whose failure is the
likeliest cause — then `logs/api.log`, then stderr.

## HTTP safety invariants

- HTTP binds `127.0.0.1:8421` by default.
- A non-loopback IP bind emits a TLS/reverse-proxy warning.
- No configured API token prevents HTTP startup; the middleware also rejects
  requests fail-closed if the token becomes unavailable.
- Missing or empty bearer credentials return 401; invalid credentials return 403.
- HTTP mode holds `.koan-pid-mcp` under `fcntl.flock()` for its lifetime.
- TLS and rate limiting remain reverse-proxy responsibilities.
- Legacy SSE endpoints are not exposed.

## Configuration and startup

`mcp.enabled` defaults to `false`. A client may launch the configured stdio
command at any time, so disabled mode means that process exits immediately with
an actionable message. Kōan never edits or installs client configuration.

`mcp.tools_allow_destructive` defaults to `false`. It is the single operator
gate on reach, and it governs **both** surfaces identically: whether a
destructive route appears in `tools/list` as a named tool, and whether
`exec_operation` may dispatch that same route or a `DENIED_NAMED_OPERATIONS`
route. One flag governs both so the risk ladder cannot invert — a deployment
that hides mission deletion can never simultaneously expose it through the
escape hatch, nor expose shutdown.

The `mcp` key predates this component as a bare list of provider client config
paths. Both shapes stay valid: a list means "provider configs only", a mapping
carries this component's settings beside `mcp.configs`. **A shape change to a
key an operator already sets must never be a hard startup stop.** Two mechanisms
uphold that, and both are required:

1. `app.config_migration.migrate_mcp_config` rewrites a legacy list into the
   mapping form in place at startup, before strict validation. It preserves
   comments, verifies the re-parsed result differs only in `mcp`, backs the file
   up once, and is idempotent.
2. `config_validator.accepts_non_mapping` keeps the list form valid regardless,
   so an unmigrated config (read-only mount, manual revert) still starts. That
   predicate is the single shared source for every `_NESTED` shorthand:
   `validate_config` and `validate_config_or_raise` must never disagree about
   which shapes are legal, because a value one accepts and the other rejects
   turns a working config into a boot failure.

MCP has no credential setting of its own. Its bearer token comes from
`config.get_api_token()` and its base URL comes from `api.host` plus `api.port`.
In stdio mode an unspecified/wildcard bind host resolves to loopback for client
requests. HTTP mode introduces `mcp.transport`, `mcp.host`, and `mcp.port`
(loopback 8421 by default). Consequently `api.enabled` must also be true and
the REST server must run.

The server performs a best-effort health probe at startup. An unreachable API
produces a stderr warning but does not terminate the server. Each failed tool
call reports the attempted URL and directs the operator to enable
`api.enabled`, run `make api-token`, and start `make api`.

The MCP Python SDK remains optional in `koan/requirements-mcp.txt`. Import
failure exits cleanly with the `make mcp-setup` repair command.

## Tool exposure contract

Routes opt in through `openapi_operation(mcp=True)`. The OpenAPI generator
emits that marker as `x-koan-mcp: true`; absence means hidden. MCP additionally
uses a fixed allow-list, so a marker alone cannot publish an unexpected tool.

Named tools use prefix `koan_`:

| Tool | Operation | Annotation |
|---|---|---|
| `koan_health` | `GET /v1/health` | read-only |
| `koan_status` | `GET /v1/status` | read-only |
| `koan_skills_list` | `GET /v1/skills` | read-only |
| `koan_missions_list` | `GET /v1/missions` | read-only |
| `koan_missions_get` | `GET /v1/missions/{mission_id}` | read-only |
| `koan_missions_result` | `GET /v1/missions/{mission_id}/result` | read-only |
| `koan_projects_list` | `GET /v1/projects` | read-only |
| `koan_usage` | `GET /v1/usage` | read-only |
| `koan_metrics` | `GET /v1/metrics` | read-only |
| `koan_logs` | `GET /v1/logs` | read-only |
| `koan_config` | `GET /v1/config` | read-only |
| `koan_missions_create` | `POST /v1/missions` | write |
| `koan_missions_reorder` | `POST /v1/missions/reorder` | write |
| `koan_pause` | `POST /v1/pause` | write |
| `koan_resume` | `POST /v1/resume` | write |
| `koan_missions_delete` | `DELETE /v1/missions/{mission_id}` | destructive, separately gated |

A tool is destructive when its route is, and nowhere else: `DELETE` by method or
an explicit `x-koan-destructive` marker, carried by `Operation.destructive` and
read by both the MCP gate and the CLI's confirmation prompt. The curation table
never restates it, so the two cannot drift apart.

Named-tool input schemas come from Python signatures through MCP SDK. Before
registration, Kōan augments each signature with Pydantic `Field` metadata from
matching OpenAPI path, query, or request-body properties. Supported numeric and
pattern bounds become both advertised and enforced. Python defaults remain
authoritative; OpenAPI defaults never replace them.

A hand-written signature MUST NOT advertise a value the REST route rejects — a
model cannot tell a typo from a capability, so an unreachable enum member is a
guaranteed failed call. Where the route validates against an in-tree vocabulary,
the signature derives its `Literal` from that same authority rather than
restating it: `koan_missions_list.status` is built from
`app.mission_store.base.VALID_STATES`, which is what `GET /v1/missions` checks.

`koan_missions_create.command` preserves the OpenAPI command-choice schema in
MCP `tools/list`. The schema exposes canonical slash commands through an
`enum` and permits arguments through a second pattern branch. Clients should
prefer `command` for catalogued work, call `koan_skills_list` for usage and
flags, and reserve `text` for work no exposed skill covers.

Every published tool has a title, a non-empty description, parameter
descriptions, and explicit `idempotentHint` and `openWorldHint` values.
Read-only tools, resume, and mission deletion count as idempotent. Timed pause,
mission creation/reordering, and `exec_operation` remain conservatively
non-idempotent. Curated tools remain closed-world; `exec_operation` stays
open-world because it can reach broader REST operations.

`exec_operation` accepts an OpenAPI `operation_id`, path arguments, query
object, and optional JSON body. This explicit escape hatch mirrors the REST
CLI's `raw` capability; clients may apply a single conservative approval policy
to it.

`DENIED_NAMED_OPERATIONS` is a reach gate, not a naming convention. It governs
both surfaces:

- Named-tool publication, always.
- `exec_operation` dispatch, unless `mcp.tools_allow_destructive` is true.

**`exec_operation` must refuse at least what named tools hide.** Its gate is
therefore the union of two sets, derived — never restated — from the same
signals the named-tool filter reads: the deny-listed routes, and every
operation whose route is destructive (`Operation.destructive`). With the
default `false`, `exec_operation` reaches every documented operation *except*
those; a refused `operation_id` raises a `ToolError` naming the flag that would
permit it, before any request leaves the process. With the flag true, it
reaches every documented operation. Deriving the destructive half from the
route rather than from a hand-maintained list is what makes a newly marked
`x-koan-destructive` endpoint closed by default: a gate that had to be extended
by hand would fail open on exactly the route that most needs it.

The gate resolves each denied `(method, path)` to its concrete `operationId`
from the same loaded document the client dispatches against, so a renamed
`operationId` cannot slip past it and the deny-list stays keyed by route.

## Safety invariants

- Missing `x-koan-mcp` always hides an operation from named tools.
- Fixed curation and a deny-list prevent accidental publication when routes
  or markers change.
- Shutdown, restart, update, release update, and project create/update/delete
  never receive named tools.
- Mission deletion stays absent unless `mcp.tools_allow_destructive` is true —
  from `tools/list` *and* from `exec_operation`, which refuses every destructive
  route while the flag is false.
- Under the default configuration no MCP surface reaches shutdown, restart,
  update, release update, or project create/update/delete — `exec_operation`
  refuses them too, so the lesser mission-delete gate can never be stricter
  than the reach of the escape hatch beside it.
- Read tools carry `readOnlyHint`; mission deletion carries `destructiveHint`.
- Every tool explicitly publishes idempotence and open-world semantics.
- Named writes do not claim read-only or destructive behavior.
- MCP never bypasses REST bearer authentication or server-side secret masking.
- In HTTP mode, the outer ASGI layer authenticates every request before MCP
  parsing; missing credentials produce 401 and invalid credentials 403.
- That layer allow-lists the scope types it forwards without a credential
  check: only `lifespan`. Any other non-HTTP scope is refused and audited, so a
  transport added later cannot inherit an unauthenticated path by default. The
  refusal is also delivered: a websocket scope is closed, and a scope type with
  no terminal message raises rather than leaving the caller on a connection
  whose refusal only the audit file records.
- An unwritable audit sink stops service rather than degrading it: the request
  that detects the failure is refused with 503 before it reaches the MCP app,
  as is every request after it, until the trail can be written again.
- stdio remains the default transport and is never daemonized.

## Change protocol

Adding a named tool requires all three: an adjacent route marker, an entry in
the fixed curation table, and tests covering schema, annotation, and deny-list
behavior. Regenerate `koan/openapi.yaml` after marker changes. Update this spec
before changing the architectural contract. REST generator ownership remains
documented in [Web Dashboard & REST API](web.md).
