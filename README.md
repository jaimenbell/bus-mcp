# bus-mcp

An ergonomic MCP server fronting the self-hosted **AlphaHive coordination
bus** (`backend/coordination_bus.py` in the `alphahive` repo) -- so a Claude
agent calls `claim_lane("feeds-refactor", owner="session-A")` instead of
hand-rolling `curl -X POST .../lanes/feeds-refactor/claim -d '{...}'`. Built
to the [desktop-mcp](https://github.com)/[github-mcp](https://github.com)
standard (own pyproject, fastmcp server, honest README, real test suite) --
this is that exact "MCP over an HTTP API" pattern turned on our own
self-hosted API.

## What this is / is not

This fronts a **private, localhost-only, no-auth v1 coordination substrate**
-- not a public service. The bus itself is a blackboard (append-only
messages) + a lane-claim registry (task-queue leases with steal-on-expiry) +
a status rollup for a command-center panel. It **executes nothing
outward-facing**: `action_flag` on a message is recorded and displayed only,
never acted on by the bus. bus-mcp adds zero new capability over what the
bus already does via `curl` -- it only makes the six routes ergonomic MCP
tools with typed inputs and typed errors instead of raw HTTP.

## Tools

| Tool | Bus route | Purpose |
|---|---|---|
| `post_message` | `POST /api/bus/message` | Append one message to the blackboard (topic, sender, body, action_flag) |
| `read_messages` | `GET /api/bus/messages` | Recent messages, newest first, optional topic filter |
| `claim_lane` | `POST /api/bus/lanes/{lane}/claim` | Claim-if-free / steal-if-lease-expired / renew-if-own; 409 if held live by another |
| `release_lane` | `POST /api/bus/lanes/{lane}/release` | Free a held lane; 409 if held live by another |
| `heartbeat_lane` | `POST /api/bus/lanes/{lane}/heartbeat` | Renew the lease; 409 if you don't hold it live |
| `get_bus_status` | `GET /api/bus/status` | Rollup: active lanes, orphaned claims, recent messages, pending action flags |

No write-safety *knob* here the way github-mcp has one for real external
writes -- every bus route is coordination-only (store/display/claim). As of
coordination-bus **v1.1**, the bus MAY optionally require a shared secret on
its 4 write routes (default off); this client mirrors that with zero new
config surface of its own -- see "Write-secret auth (v1.1)" below.

## Typed errors, never a raw crash

Every tool returns `{"ok": true, ...}` on success or `{"ok": false, "error":
{...}}` on failure -- never an unhandled exception or stack trace.

- **`bus_unreachable`** -- connection refused, timeout, or DNS failure. Means
  the AlphaHive backend isn't running, or is running without the bus routes
  loaded (`backend/coordination_bus.py` mounted on `:8100`).
- **`bus_api_error`** -- the bus responded with a 4xx/5xx. Carries
  `status_code` + the bus's own `detail` text -- e.g. a `409` lane-conflict
  message telling you who holds the lane and for how long.

Internally, `bus_mcp/client.py` raises typed `BusUnreachable` / `BusApiError`
exceptions; `bus_mcp/routes.py` catches both and normalizes to the dict
shape above before a tool ever returns. Tests exercise both layers.

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `BUS_MCP_BASE_URL` | `http://127.0.0.1:8100/api/bus` | Base URL of the coordination bus |
| `BUS_MCP_TIMEOUT_S` | `10.0` | Per-request timeout (seconds) |
| `BUS_MCP_LIVE` | unset | Set to `1` to run the real-network smoke test (see Testing) |
| `BUS_WRITE_SECRET` | unset | Same var the bus itself reads to arm write-auth (v1.1). When set here, every write tool call sends `X-Bus-Secret: <value>` automatically. Unset = no header sent, matching an unarmed bus byte-for-byte. |

## Write-secret auth (v1.1)

The coordination bus can optionally gate its 4 write routes (`post_message`,
`claim_lane`, `release_lane`, `heartbeat_lane`) behind a shared secret header
(`X-Bus-Secret`), read from `BUS_WRITE_SECRET` on the bus side. This client
reads the **same env var name** from its own process and, when set,
`bus_mcp/client.py`'s `post()` attaches the header to every write call --
`bus_mcp/routes.py` and every tool caller stay unaware of arming state
entirely. `client.get()` never attaches the header (GET routes are never
gated bus-side).

**To use with an armed bus:** set `BUS_WRITE_SECRET` to the same value in
both the AlphaHive backend's environment and this MCP server's environment
(e.g. in the config that launches `run_server.py`), then restart both
processes. If the value is missing or wrong, a write tool call returns the
normal `{"ok": false, "error": {"type": "bus_api_error", "status_code": 401,
...}}` shape -- no special-casing needed, it flows through the same typed
`BusApiError` path as any other 4xx.

**Unset (default):** no header is sent, identical to talking to a bus that
has never been armed -- zero behavior change from pre-v1.1.

## Usage examples

Once connected in a Claude session, an agent can:

```
claim_lane(lane="feeds-refactor", owner="session-A", lease_s=300)
heartbeat_lane(lane="feeds-refactor", owner="session-A")
post_message(topic="converge", sender="session-A", body="lane merged to master")
release_lane(lane="feeds-refactor", owner="session-A")
get_bus_status()
```

## Testing

```bash
.venv/Scripts/python.exe -m pytest -q
```

All HTTP is mocked via [respx](https://lundberg.github.io/respx/) -- the
full suite never depends on a live bus. One additional test,
`tests/test_live_smoke.py::test_live_get_bus_status_returns_rollup`, is
gated behind `BUS_MCP_LIVE=1` and calls a real running bus's `get_bus_status`
route. **As of this writing the bus routes are dormant/404 on the live
`:8100` AlphaHive backend** until the operator restarts it with
`coordination_bus.py`'s router mounted -- so that one gated test is expected
to skip (or fail if forced) until that restart happens. That is correct
behavior, not a bug in this repo.

## Install / connect

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[test]"
```

Registered in `~/.claude.json` under `mcpServers.bus-mcp` as a stdio server
invoking `run_server.py` by absolute path (no `cwd` needed -- the entrypoint
adds its own directory to `sys.path`).

## Handshake check

```bash
.venv/Scripts/python.exe scripts/list_tools.py
```

Prints the six registered tool names with no transport started -- pure
introspection, useful for verifying the server wires up cleanly after any
change.

## Out of scope

- Any push / public repo (local-only for now -- this fronts our own
  self-hosted setup, more setup-specific than github-mcp)
- Authenticating *who* `owner`/`sender` claims to be -- the shared secret
  (v1.1) proves possession of a value, not identity; that stays client-
  asserted the same as before. See the bus's own README for that boundary.
- Restarting the AlphaHive backend to bring the live bus routes up
  (operator, elevated -- not something this MCP does)
- Bus v2 execution/approval features (a separate, not-yet-built arc)
