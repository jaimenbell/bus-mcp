# Changelog

All notable changes to bus-mcp. Versions follow the package version in
`pyproject.toml`, which `tests/test_version.py` pins against `server.json` and
`bus_mcp.__version__` so the three cannot drift.

## 0.2.0

The server wrapped messages, lanes and status, so an agent could broadcast
but not converse, could not see the work board, and could not open or vote in
a validation. This release adds the coordination bus's thread, validation,
dispatch, board, worker and event routes -- 24 tools in all -- and, just as
deliberately, keeps three routes unwrapped forever.

(No route total is quoted here on purpose: the bus's route count is a moving
number owned by another repo, and a proof number this changelog cannot
re-derive is one it should not print.)

### Added

- **Threads** -- `list_threads`, `get_thread`, `open_thread`,
  `reply_in_thread`, `resolve_thread`. Topics stay the broadcast log; threads
  are a conversation with a beginning and an end.
- **Message addressing** -- `post_message` gains `thread_id`, `reply_to` and
  `recipient`, which the bus's own request model has accepted since M1b and
  this client silently dropped.
- **Validations and dispatches** -- `list_validations`, `get_validation`,
  `request_validation`, `vote`, `list_dispatches`, `mint_dispatch`,
  `report_dispatch`.
- **Board, worker, events** -- `list_tasks_board`, `get_worker_state`,
  `read_events`, plus `claim_task` / `heartbeat_task` / `finish_task` behind a
  new gate (below).
- **`BUS_MCP_AGENT_ID`** -- one consistent self-asserted identity
  (`session:<hostname>:<pid>` by default, computed once at import and
  sanitized to the bus's role shape). It is the default `sender` / `owner` /
  `opened_by` / `requested_by` / `voter` / `minted_by` / `resolved_by`, and
  every tool result -- success, error, or refusal -- echoes the value used
  under `agent_id`. Not authentication: the bus shape-checks role strings and
  nothing more. The echo is the honest part.
- **`BUS_MCP_ENABLE_TASK_CLAIM`** -- a second, narrower gate. The three task
  mutations ship DARK: wired, tested, refusing by default with a message that
  names both the gate and the reason. The write gate asks whether this server
  may write at all; this asks whether an MCP session is a registered claimant
  of board work, which today it is not. The gates stack write-outermost, so
  arming the narrow one is never a way around the broad one.
- **`tests/test_rails_pins.py`** -- the three operator-authority routes
  (validation decide, task minting, board sweep) are pinned absent by both
  source grep and registered-tool introspection, with a positive control
  proving the grep can see the package it guards.
- **A tool-count gate** -- the README's Tools badge is compared against the
  live registered tool list, and every registered tool must have a row in the
  README tables. A matching count is not coverage.

### Changed

- The write gate now covers every mutating route, enforced by a parity test
  that ENUMERATES the gated functions rather than re-listing them by hand.
- `task_id` gets a traversal guard stricter than the bus's own charset: that
  pattern allows `.` and `/`, so `../../status` satisfies it. A charset says
  what an id may contain, not that it is not a traversal.
- Tests can no longer reach a real bus. An autouse respx fixture makes any
  unmocked request raise. Opt-in mocking failed silently exactly where it
  mattered most: a refusal test that had lost its own decorator was posting a
  task claim to the live backend and passing on the 401.

### Known limitations (stated, not worked around)

- `read_messages`' `thread_id` / `recipient` / `since_id` are sent as query
  params and **the current backend ignores all three** -- its message read
  declares `topic` and `limit` only. They are wired so they start working the
  moment the server-side filters land. Documented in the tool description, the
  docstring and a test.
- `get_thread` is COMPOSED client-side because the bus has no by-id thread
  route. It reports `scanned` and `scan_truncated` so a truncated read cannot
  be mistaken for a complete one.
- `list_tasks_board`'s `status` / `limit` are applied client-side; the route
  itself takes only `include_archived`.
- `claim_task` REFUSES `lease_s` rather than dropping it: the bus sets the
  lease server-side and its request model has no such field.

## 0.1.1

Initial public release: `post_message`, `read_messages`, `claim_lane`,
`release_lane`, `heartbeat_lane`, `get_bus_status`, with the v1.1 write-secret
header and the `BUS_MCP_ENABLE_WRITE` gate.
