# bus-mcp

[![PyPI](https://img.shields.io/pypi/v/bus-mcp)](https://pypi.org/project/bus-mcp/)
[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-io.github.jaimenbell%2Fbus--mcp-blue)](https://registry.modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-282%20%28281%20passing%2C%201%20skipped%29-brightgreen)](#testing)
[![Tools](https://img.shields.io/badge/tools-24-blue)](#tools)
[![CI](https://github.com/jaimenbell/bus-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jaimenbell/bus-mcp/actions/workflows/ci.yml)

An ergonomic MCP server fronting the self-hosted **AlphaHive coordination
bus** (`backend/coordination_bus.py` in the `alphahive` repo) -- so a Claude
agent calls `claim_lane("feeds-refactor", owner="session-A")` instead of
hand-rolling `curl -X POST .../lanes/feeds-refactor/claim -d '{...}'`. Built
to the [desktop-mcp](https://github.com/jaimenbell/desktop-mcp)/[github-mcp](https://github.com/jaimenbell/github-mcp)
standard (own pyproject, fastmcp server, honest README, real test suite) --
this is that exact "MCP over an HTTP API" pattern turned on our own
self-hosted API.

## Quickstart (60 seconds)

```bash
pip install bus-mcp
```

Add to your Claude Desktop/Code MCP config:

```json
{
  "mcpServers": {
    "bus-mcp": {
      "command": "bus-mcp"
    }
  }
}
```

No console script on PATH? Fall back to `"command": "python", "args": ["-m", "bus_mcp"]`.
By default this talks to a bus at `http://127.0.0.1:8100/api/bus` -- see
"Env vars" below to point it elsewhere.

## What this is / is not

This fronts a **private, localhost-only, no-auth v1 coordination substrate**
-- not a public service. The bus itself is a blackboard (append-only
messages) + a lane-claim registry (task-queue leases with steal-on-expiry) +
a status rollup for a command-center panel. It **executes nothing
outward-facing**: `action_flag` on a message is recorded and displayed only,
never acted on by the bus. bus-mcp adds zero new capability over what the
bus already does via `curl` -- it only makes the routes ergonomic MCP tools
with typed inputs and typed errors instead of raw HTTP. It deliberately wraps
LESS than the bus offers: see "What this will never wrap" below.

## Tools

24 tools. Writes are gated (`BUS_MCP_ENABLE_WRITE`); reads never are.

**Messages and lanes**

| Tool | Bus route | Purpose |
|---|---|---|
| `post_message` | `POST /api/bus/message` | Append one message to the blackboard. Optional addressing: `thread_id` (a reply in that thread), `reply_to` (the message it answers), `recipient` (a role; omitted = broadcast) |
| `read_messages` | `GET /api/bus/messages` | Recent messages, newest first, optional topic filter. `thread_id` / `recipient` / `since_id` are accepted but **the current backend ignores them** -- see "Filters the backend ignores today" |
| `claim_lane` | `POST /api/bus/lanes/{lane}/claim` | Claim-if-free / steal-if-lease-expired / renew-if-own; 409 if held live by another. Response echoes the effective (post-clamp) `lease_s` granted -- see "Lease ceiling" below. |
| `release_lane` | `POST /api/bus/lanes/{lane}/release` | Free a held lane; 409 if held live by another |
| `heartbeat_lane` | `POST /api/bus/lanes/{lane}/heartbeat` | Renew the lease; 409 if you don't hold it live. Response echoes the effective `lease_s`, same as claim. |
| `get_bus_status` | `GET /api/bus/status` | Rollup: active lanes, orphaned claims, recent messages, pending action flags, effective `_meta.max_lease_seconds` ceiling |

**Threads** -- topics are the broadcast log; threads are how two agents (or an
agent and a human) hold one conversation with a beginning and an end.

| Tool | Bus route | Purpose |
|---|---|---|
| `list_threads` | `GET /api/bus/threads` | Threads, newest first. **Omitting `status` excludes archived** -- ask for `status="archived"` separately |
| `get_thread` | *composed* | One thread plus its messages. There is no by-id thread route, so this finds the thread in the list and filters that topic's messages client-side; check `scan_truncated` |
| `open_thread` | `POST /api/bus/threads` | Open a thread + its root message. `opened_by` is the resolve authority afterwards; `kind="DECIDE"` marks a thread only the operator may resolve |
| `reply_in_thread` | `POST /api/bus/message` | Reply inside a thread. `topic` is looked up from the thread when omitted |
| `resolve_thread` | `POST /api/bus/threads/{id}/resolve` | Resolve a thread you opened. `resolved_by="operator"` is **refused client-side**; a `note` is posted as a thread reply first |

**Validations and dispatches** -- request a refutation, vote under a
registered dispatch id.

| Tool | Bus route | Purpose |
|---|---|---|
| `list_validations` | `GET /api/bus/validations` | Validations, newest first; optional subject_ref / verdict / thread_id filters |
| `get_validation` | `GET /api/bus/validations/{id}` | One validation by id -- no page to fall off, 404 when genuinely absent |
| `request_validation` | `POST /api/bus/validations` | Open a validation. `subject_kind` is derived from the `subject_ref` prefix (`message:` / `task:` / `proposal:`), never guessed; `tier` is derived server-side and is not a parameter |
| `vote` | `POST /api/bus/validations/{id}/vote` | Cast one vote under a registered dispatch id. `evidence` is a pointer, not the argument |
| `list_dispatches` | `GET /api/bus/dispatches` | Registered dispatches, newest first |
| `mint_dispatch` | `POST /api/bus/dispatches` | Register a dispatch so a vote cast under it can be counted |
| `report_dispatch` | `POST /api/bus/dispatches/{id}/report` | Close the loop: report against a dispatch id, naming the evidence |

**Board, worker, events**

| Tool | Bus route | Purpose |
|---|---|---|
| `list_tasks_board` | `GET /api/bus/tasks/board` | The task board. `status` / `limit` are applied **client-side** (the route takes only `include_archived`) |
| `claim_task` | `POST /api/bus/tasks/{id}/claim` | **Dark by default** (`BUS_MCP_ENABLE_TASK_CLAIM`). Mints and returns the `claim_token` -- keep it, the board will not give it back |
| `heartbeat_task` | `POST /api/bus/tasks/{id}/heartbeat` | **Dark by default.** Renews the lease for the live (owner, claim_token) holder; `want_running=True` performs claimed -> running |
| `finish_task` | `POST /api/bus/tasks/{id}/finish` | **Dark by default.** Terminal write: done / failed / needs_operator |
| `get_worker_state` | `GET /api/bus/worker` | The overnight worker's last heartbeat. Missing is never zero: unmeasured fields are null and the envelope carries data age |
| `read_events` | `GET /api/bus/events` | Cursor poll over the append-only event log. Rows come back **ascending** by id |

Every bus route wrapped here is coordination-only (store / display / claim).
As of coordination-bus **v1.1** the bus MAY require a shared secret on its
write routes (default off); this client mirrors that with zero new config
surface of its own -- see "Write-secret auth (v1.1)" below. Separately, this
server has its own local write gate (`BUS_MCP_ENABLE_WRITE`, default off) and
a second, narrower gate for task claiming.

## What this will never wrap

Three bus routes are operator-authority and have **no tool here**, by design.
`tests/test_rails_pins.py` enforces their absence by both source grep and
registered-tool introspection, so the rule is a test rather than a promise.

| Route | Why not |
|---|---|
| the validation *decide* route | Authenticated by a second `X-Bus-Operator-Secret` this server does not hold. A quorum this client can request and vote in, but cannot decide, is the whole separation |
| the task *minting* route | Mints executable work; its `auto` class is operator-authenticated. Minting stays a CLI ritual against a staged file a human has read |
| the task *sweep* route | Terminally abandons other claimants' rows -- not per-task, not reversible |

## Identity (`BUS_MCP_AGENT_ID`)

The bus authenticates nobody on `sender` / `owner` / `opened_by`: it
shape-checks a string. Two MCP sessions sharing a write secret are otherwise
indistinguishable in the log.

This server asserts one consistent identity -- `BUS_MCP_AGENT_ID` when set,
otherwise `session:<hostname>:<pid>` computed once at import and sanitized to
the bus's own role shape (printable ASCII, no whitespace, 1-64 chars). It is
the default for `sender`, `owner`, `opened_by`, `requested_by`, `voter`,
`minted_by` and `resolved_by` when the caller omits them, and **every tool
result echoes the value used under `agent_id`** -- including error and refusal
payloads, where a caller most needs to know which identity was rejected.

**This is not authentication, and the echo is the honest part.** Any holder of
the write secret can assert any identity. What this buys is consistency and a
straight answer to "what did you put on the wire as me".

## Task claiming is dark by default (`BUS_MCP_ENABLE_TASK_CLAIM`)

`claim_task` / `heartbeat_task` / `finish_task` refuse unless
`BUS_MCP_ENABLE_TASK_CLAIM` is truthy, with a refusal that names both the gate
and the reason.

It is not a duplicate of `BUS_MCP_ENABLE_WRITE`. The write gate asks whether
this server may write to the bus at all; this one asks whether an MCP-driven
session is a registered *claimant* of board work -- a separate question, whose
answer today is no. The two gates stack with the write gate outermost, so a
server with writes off answers "writes are off", and arming the narrow gate is
never a way around the broad one.

## Filters the backend ignores today

`read_messages` accepts `thread_id`, `recipient` and `since_id` and sends them
as query params. **The current backend ignores all three**: its message read
declares `topic` and `limit` only, and FastAPI drops query params a route does
not declare. Passing them changes nothing about what comes back.

They are wired anyway, deliberately: the server-side filters are a separate
backend change, and when it lands these params start working with no change
here and no version negotiation. Until then, filter client-side -- which is
exactly what `get_thread` does, and why it reports `scanned` and
`scan_truncated` rather than implying it saw the whole thread.

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
| `BUS_MCP_ENABLE_WRITE` | unset (off) | Local write gate. Every mutating tool refuses with a typed `policy_refusal` until this is truthy -- separate from `BUS_WRITE_SECRET`, which authenticates a write against the bus over the wire |
| `BUS_MCP_ENABLE_TASK_CLAIM` | unset (off) | Second, narrower gate for `claim_task` / `heartbeat_task` / `finish_task`. See "Task claiming is dark by default" |
| `BUS_MCP_AGENT_ID` | `session:<hostname>:<pid>` | The identity this server asserts on writes and echoes as `agent_id`. See "Identity" |
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

## Lease ceiling surfacing (coordination-bus v1.3+)

The bus supports an operator-configurable ceiling on granted lease durations
(`BUS_MAX_LEASE_SECONDS`, bus-side): a `claim_lane`/`heartbeat_lane` request
for `lease_s=7200` may be silently **clamped** to a shorter effective grant
(e.g. 3600s) rather than rejected -- see `coordination_bus.README.md`'s
"v1.3 - configurable lease ceiling" section in the alphahive repo for the
full server-side story.

This client surfaces both halves of that contract, additively:

- **`claim_lane` / `heartbeat_lane` responses** include a top-level `lease_s`
  field on `ok=True` -- the EFFECTIVE (post-clamp) duration actually granted.
  Always check this rather than assuming the requested `lease_s` was honored
  in full; a caller that ignores it and heartbeats on its own optimistic
  schedule risks its lane going stale early.
- **`get_bus_status`** exposes `_meta.max_lease_seconds` -- the currently
  configured ceiling, so a caller can check before it even claims.

Both fields are pure passthrough: `bus_mcp/routes.py` merges the bus's raw
JSON response into the tool result (`{"ok": True, **result}`), so no
client-side code change was needed to carry these new fields -- only the
tool descriptions (below) and test coverage locking the behavior in both
directions. **Version-tolerant by construction:** against a pre-v1.3 bus
that omits these fields entirely, the tool result simply lacks `lease_s` /
`max_lease_seconds` -- never a crash, never a synthesized default.

No client-side ceiling caching/pre-flight warning is implemented -- this
client holds no state between calls (every tool call is a fresh `httpx`
request), so there is nothing to check a requested `lease_s` against locally
before the round-trip. A caller that wants to avoid a surprise clamp should
call `get_bus_status` first and compare its own `lease_s` request against
`_meta.max_lease_seconds`.

## Usage examples

Once connected in a Claude session, an agent can:

```
claim_lane(lane="feeds-refactor", owner="session-A", lease_s=300)
heartbeat_lane(lane="feeds-refactor", owner="session-A")
post_message(topic="converge", sender="session-A", body="lane merged to master")
release_lane(lane="feeds-refactor", owner="session-A")
get_bus_status()
```

Or hold a conversation, and get a claim refuted rather than believed:

```
open_thread(topic="converge", title="feeds-refactor is ready", kind="DECIDE",
            body="suite green on a clean checkout, one skip. Merge?")
list_threads(status="open")
reply_in_thread(thread_id=4, body="re-ran it on a clean checkout: same result")
get_thread(thread_id=4)
resolve_thread(thread_id=4, note="merged at a1b2c3d")

mint_dispatch(lane="feeds-refactor", repo="bus-mcp", purpose="verify the claim")
request_validation(subject_ref="message:24047", evidence_refs="junit.xml")
vote(validation_id=7, dispatch_id="a1b2c3d4e5f6", verdict="refuted",
     evidence="tests/out.xml line 88")
get_validation(validation_id=7)
```

## Testing

```bash
.venv/Scripts/python.exe -m pytest -q
```

CI (`.github/workflows/ci.yml`) runs this suite on every push/PR and fails
the build if the Tests badge above drifts from what the suite actually
reports -- see `scripts/check_readme_counts.py`.

All HTTP is mocked via [respx](https://lundberg.github.io/respx/) -- the
full suite never depends on a live bus, and an autouse fixture makes that
enforceable rather than customary: any unmocked request raises instead of
leaving the process. (Opt-in mocking failed silently exactly where it
mattered most -- on refusal tests, which assert that a call does *not*
happen.) One additional test,
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

Prints every registered tool name with no transport started -- pure
introspection, useful for verifying the server wires up cleanly after any
change. The count it prints is gated against the Tools badge above by
`tests/test_check_readme_counts.py`, which also fails if a registered tool has
no row in the tables above -- a matching count is not coverage.

## Out of scope

- Authenticating *who* `owner`/`sender` claims to be -- the shared secret
  (v1.1) proves possession of a value, not identity; that stays client-
  asserted, now consistently so via `BUS_MCP_AGENT_ID`. See "Identity" above
  and the bus's own README for that boundary.
- Any operator-authority route: deciding a validation, minting a task,
  sweeping the board. See "What this will never wrap".
- Restarting the AlphaHive backend to bring the live bus routes up
  (operator, elevated -- not something this MCP does)
- Bus v2 execution/approval features (a separate, not-yet-built arc)


## Commercial support

Maintained by [Jaimen Bell](https://jaimenbell.dev). For production MCP integrations, custom servers, or agent-reliability work, see [jaimenbell.dev](https://jaimenbell.dev).

Building your own MCP server? The [MCP Starter Kit](https://jaimenbell.gumroad.com/l/adnojp) has templates, a build playbook, and packaging war-stories from shipping this one.

<!-- MCP registry ownership marker -->
mcp-name: io.github.jaimenbell/bus-mcp
