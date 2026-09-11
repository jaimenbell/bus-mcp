"""bus_mcp.server -- FastMCP instance + tool wiring. Six tools, one per
coordination-bus route (backend/coordination_bus.py in the alphahive repo),
each a thin async passthrough to bus_mcp.routes.

Auth model (updated 2026-07-10 -- the v1.1 write-secret gate landed the same
morning this docstring used to say "no auth, no write-gate"):
  - WRITE routes (claim_lane / release_lane / heartbeat_lane / post_message)
    require an `X-Bus-Secret` header matching the server's `BUS_WRITE_SECRET`
    env var, IF that var is set on the backend. Unset (unarmed) = those routes
    stay open, byte-identical to pre-v1.1 behavior. This client reads the same
    `BUS_WRITE_SECRET` env var from its own process and sends the header
    automatically when set.
  - READ routes (read_messages / get_bus_status) are intentionally NEVER
    gated, by design, regardless of arming state -- a caller with just the
    base URL can always read.
  - `owner` on write calls is a SELF-ASSERTED string, not an authenticated
    identity: any caller holding the write-secret (or no secret at all, if
    the server is unarmed) can act as any owner. The secret proves you're an
    authorized writer, not who you are.
"""
from __future__ import annotations

from fastmcp import FastMCP

from . import config, routes

SERVER_NAME = "bus-mcp"
mcp = FastMCP(SERVER_NAME)


@mcp.tool(
    name="post_message",
    description=(
        "Append one message to the coordination-bus blackboard (append-only). "
        "v1 stores action_flag but performs no action -- it is display-only, "
        "seen by a human watching the command-center panel. Optional "
        "addressing: `thread_id` makes this a reply in that thread, "
        "`reply_to` names the message it answers, `recipient` addresses a "
        "ROLE (omitted = broadcast). All three are omitted from the wire "
        "when unset."
    ),
)
async def post_message_tool(
    topic: str,
    sender: str,
    body: str,
    action_flag: bool = False,
    thread_id: int | None = None,
    reply_to: int | None = None,
    recipient: str | None = None,
) -> dict:
    return routes.post_message(
        topic, sender, body, action_flag, thread_id, reply_to, recipient
    )


@mcp.tool(
    name="read_messages",
    description=(
        "Recent bus messages, newest first, optionally filtered by topic. "
        "LIMITATION: `thread_id`, `recipient` and `since_id` are sent as query "
        "params but TODAY'S BACKEND IGNORES ALL THREE -- it filters on topic "
        "and limit only, and drops query params it does not declare. Passing "
        "them changes nothing about what comes back; filter client-side, or "
        "use get_thread. They are wired so that they start working the moment "
        "the server-side filters land, with no change here."
    ),
)
async def read_messages_tool(
    topic: str | None = None,
    limit: int = 50,
    thread_id: int | None = None,
    recipient: str | None = None,
    since_id: int | None = None,
) -> dict:
    return routes.read_messages(topic, limit, thread_id, recipient, since_id)


@mcp.tool(
    name="claim_lane",
    description=(
        "Claim a coordination lane before starting work in it: claim-if-free, "
        "steal-if-lease-expired, renew-if-you-already-own-it. A 409 (lane held "
        "live by another owner) comes back as a clean ok=False conflict, not a crash. "
        "The bus may grant a shorter lease than requested (server-side ceiling, "
        "coordination-bus v1.3+): on ok=True the response's top-level `lease_s` is "
        "the EFFECTIVE (post-clamp) duration actually granted -- always check it "
        "rather than assuming the requested value was honored. See get_bus_status's "
        "`_meta.max_lease_seconds` for the currently configured ceiling. Older bus "
        "servers (pre-v1.3) omit `lease_s` from the response entirely; its absence "
        "just means the bus predates the ceiling feature, not an error."
    ),
)
async def claim_lane_tool(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict:
    return routes.claim_lane(lane, owner, lease_s)


@mcp.tool(
    name="release_lane",
    description=(
        "Release a coordination lane you hold. A 409 (held live by another "
        "owner) comes back as a clean ok=False conflict, not a crash."
    ),
)
async def release_lane_tool(lane: str, owner: str) -> dict:
    return routes.release_lane(lane, owner)


@mcp.tool(
    name="heartbeat_lane",
    description=(
        "Renew the lease on a coordination lane you hold live. A 409 (not "
        "held live by you) tells you to (re)claim instead of crashing. Like "
        "claim_lane, renewal is subject to the same server-side lease ceiling "
        "(coordination-bus v1.3+): on ok=True the response's top-level `lease_s` "
        "is the EFFECTIVE (post-clamp) duration actually granted, which may be "
        "shorter than requested -- check it rather than assuming the request was "
        "honored in full. See get_bus_status's `_meta.max_lease_seconds` for the "
        "currently configured ceiling. Older bus servers (pre-v1.3) omit `lease_s` "
        "from the response entirely; its absence just means the bus predates the "
        "ceiling feature, not an error."
    ),
)
async def heartbeat_lane_tool(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict:
    return routes.heartbeat_lane(lane, owner, lease_s)


@mcp.tool(
    name="get_bus_status",
    description=(
        "Roll-up for the command-center panel: active lanes, orphaned/stale "
        "claims, recent messages, pending display-only action flags. Also "
        "exposes `_meta.max_lease_seconds` (coordination-bus v1.3+): the "
        "currently configured lease ceiling that claim_lane/heartbeat_lane "
        "requests get silently clamped to. Check this before claiming a lane "
        "for longer than the default if you need to know whether the request "
        "will actually be honored in full. Older bus servers (pre-v1.3) omit "
        "`max_lease_seconds` from `_meta` entirely; its absence just means the "
        "bus predates the ceiling feature, not an error."
    ),
)
async def get_bus_status_tool() -> dict:
    return routes.get_bus_status()


# â”€â”€ v0.2.0: threads -- the conversation primitive â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Topics stay the broadcast log; THREADS are how two agents (or an agent and
# the operator) hold one conversation with a beginning and an end. Every tool
# below is a thin passthrough to bus_mcp.routes, same as the original six.


@mcp.tool(
    name="list_threads",
    description=(
        "List conversation threads, newest first (ungated read). `status` is "
        "one of open/resolved/archived. IMPORTANT DEFAULT: omitting `status` "
        "returns the LIVE board and EXCLUDES archived threads -- there is no "
        "single call that returns everything, ask for status='archived' "
        "separately."
    ),
)
async def list_threads_tool(status: str | None = None, limit: int = 50) -> dict:
    return routes.list_threads(status, limit)


@mcp.tool(
    name="get_thread",
    description=(
        "One thread plus its messages, oldest-first. COMPOSED CLIENT-SIDE: the "
        "bus has no by-id thread route and its message read filters by topic "
        "only, so this finds the thread in the thread list, reads that topic's "
        "messages, and keeps the ones whose thread_id matches. Check "
        "`scan_truncated` in the result -- when it is true, older replies in "
        "the thread exist that this call could not see."
    ),
)
async def get_thread_tool(thread_id: int, limit: int = 500) -> dict:
    return routes.get_thread(thread_id, limit)


@mcp.tool(
    name="open_thread",
    description=(
        "Open a conversation thread and its root message in one call. "
        "`opened_by` defaults to this server's agent id AND IS THE RESOLVE "
        "AUTHORITY afterwards -- only that same identity (or the operator) can "
        "resolve the thread. `kind` is free text with one special value: "
        "DECIDE marks a thread only the OPERATOR may resolve. "
        "`action_flag=True` pages the operator's phone and is reserved for "
        "genuinely blocking work."
    ),
)
async def open_thread_tool(
    topic: str,
    title: str,
    body: str,
    kind: str | None = None,
    opened_by: str | None = None,
    recipient: str | None = None,
    action_flag: bool = False,
) -> dict:
    return routes.open_thread(topic, title, body, kind, opened_by, recipient, action_flag)


@mcp.tool(
    name="reply_in_thread",
    description=(
        "Reply inside a thread -- an ordinary bus message carrying thread_id. "
        "`topic` is required by the bus but not by this tool: omit it and the "
        "thread is looked up and its own topic used (one extra read). "
        "`reply_to` is the message id you are answering; omitted, it is left "
        "unset rather than guessed at the thread root."
    ),
)
async def reply_in_thread_tool(
    thread_id: int,
    body: str,
    reply_to: int | None = None,
    sender: str | None = None,
    action_flag: bool = False,
    topic: str | None = None,
    recipient: str | None = None,
) -> dict:
    return routes.reply_in_thread(
        thread_id, body, reply_to, sender, action_flag, topic, recipient
    )


@mcp.tool(
    name="resolve_thread",
    description=(
        "Resolve a thread you opened. REFUSED CLIENT-SIDE when resolved_by is "
        "'operator' in any casing: proving the operator role needs a second "
        "secret this server deliberately does not hold, so operator "
        "resolution stays a CLI/paste action. A `note` is posted as a REPLY in "
        "the thread first (the resolve body has no note field and would drop "
        "it), then the thread is resolved -- see `note_posted` in the result."
    ),
)
async def resolve_thread_tool(
    thread_id: int, resolved_by: str | None = None, note: str | None = None
) -> dict:
    return routes.resolve_thread(thread_id, resolved_by, note)


# --- v0.2.0: validations + dispatches -------------------------------------
#
# NO `decide` TOOL EXISTS HERE, deliberately and permanently: deciding a
# validation is authenticated by the operator secret, which this server does
# not hold. Requesting and voting are agent work; deciding is the operator's.


@mcp.tool(
    name="list_validations",
    description=(
        "Validations, newest first (ungated read). Optional filters: "
        "subject_ref, verdict (pending/confirmed/refuted/indeterminate), "
        "thread_id. A misspelled verdict is a 422, never a silently empty "
        "list."
    ),
)
async def list_validations_tool(
    limit: int = 50,
    subject_ref: str | None = None,
    verdict: str | None = None,
    thread_id: int | None = None,
) -> dict:
    return routes.list_validations(limit, subject_ref, verdict, thread_id)


@mcp.tool(
    name="get_validation",
    description=(
        "One validation by id (ungated read). Prefer this over scanning "
        "list_validations: the list is bounded, and a validation older than "
        "the page is exactly the case where 'no such row' would be a lie "
        "about an outstanding refutation."
    ),
)
async def get_validation_tool(validation_id: int) -> dict:
    return routes.get_validation(validation_id)


@mcp.tool(
    name="request_validation",
    description=(
        "Open a validation on a subject. `subject_ref` is prefixed -- "
        "'message:<id>', 'task:<id>' or 'proposal:<repo>:<path>' -- and the "
        "subject_kind is DERIVED from that prefix rather than defaulted, "
        "because guessing the kind would guess the tier, and the tier decides "
        "who signs off. `tier` is not a parameter: the bus derives it. "
        "`evidence_refs` is free text (a pointer), not a list."
    ),
)
async def request_validation_tool(
    subject_ref: str,
    evidence_refs: str | None = None,
    requested_by: str | None = None,
    subject_kind: str | None = None,
) -> dict:
    return routes.request_validation(subject_ref, evidence_refs, requested_by, subject_kind)


@mcp.tool(
    name="vote",
    description=(
        "Cast one vote on a validation. `dispatch_id` must be a REGISTERED, "
        "still-open dispatch (mint one with mint_dispatch) -- that is what "
        "makes a quorum count something declared in advance. `verdict` is "
        "confirmed/refuted/indeterminate; `evidence` is a POINTER to the "
        "evidence (a path, a commit, a message ref), not the argument itself. "
        "A voter cannot confirm its own request -- the bus answers 403."
    ),
)
async def vote_tool(
    validation_id: int,
    dispatch_id: str,
    verdict: str,
    evidence: str,
    voter: str | None = None,
) -> dict:
    return routes.vote(validation_id, dispatch_id, verdict, evidence, voter)


@mcp.tool(
    name="list_dispatches",
    description=(
        "Registered dispatches, newest first (ungated read). `status` is "
        "open/reported/expired. Use it to recover a dispatch id minted in an "
        "earlier session before voting under it."
    ),
)
async def list_dispatches_tool(status: str | None = None, limit: int = 50) -> dict:
    return routes.list_dispatches(status, limit)


@mcp.tool(
    name="mint_dispatch",
    description=(
        "Register a dispatch so a vote cast under it can be counted; the "
        "server mints the 12-hex id. The honest limit: the write secret is "
        "the only identity, so this proves a dispatch was DECLARED, not that "
        "it ran. `minted_by` defaults to this server's agent id."
    ),
)
async def mint_dispatch_tool(
    lane: str, repo: str, purpose: str, minted_by: str | None = None
) -> dict:
    return routes.mint_dispatch(lane, repo, purpose, minted_by)


@mcp.tool(
    name="report_dispatch",
    description=(
        "Close the loop on a dispatch: report against its id, naming the "
        "evidence. `report_ref` is a POINTER (report path, commit, message "
        "ref), not the report text."
    ),
)
async def report_dispatch_tool(dispatch_id: str, report_ref: str) -> dict:
    return routes.report_dispatch(dispatch_id, report_ref)



if __name__ == "__main__":
    mcp.run()
