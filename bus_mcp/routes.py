"""bus_mcp.routes -- one function per coordination-bus route (backend/
coordination_bus.py), 1:1. Each function calls bus_mcp.client, which raises
BusUnreachable / BusApiError on failure; every function here catches both and
normalizes to `{"ok": False, "error": {...}}` so a tool caller never sees a
raw exception or stack trace -- only server.py's @mcp.tool wrappers call
these, and they return whatever these functions return, unmodified.

Every payload -- success, typed error, or gate refusal -- carries `agent_id`:
the self-asserted identity this process put (or would have put) on the wire
for this call. See `config.get_agent_id`.
"""
from __future__ import annotations

import re
from typing import Any

from . import client, config

# Security: `lane` is interpolated into the request URL path
# (f"/lanes/{lane}/claim"). Unsanitized, a value containing "/" or "?" or ".."
# lets a caller manipulate the same-host endpoint (path injection). Confine it
# to a strict charset before it is ever placed in a URL. Fail closed with the
# module's standard ok=False error dict rather than raising.
_LANE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _invalid_lane_payload(lane: Any, tool: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "type": "invalid_lane",
            "message": (
                f"lane {lane!r} is invalid; must match {_LANE_RE.pattern} "
                "(letters, digits, underscore, hyphen only)."
            ),
            "tool": tool,
        },
        "agent_id": config.get_agent_id(),
    }


def _valid_lane(lane: Any) -> bool:
    return isinstance(lane, str) and bool(_LANE_RE.match(lane))


def _error_payload(exc: client.BusUnreachable | client.BusApiError) -> dict[str, Any]:
    if isinstance(exc, client.BusUnreachable):
        return {
            "ok": False,
            "error": {
                "type": "bus_unreachable",
                "message": str(exc),
                "tool": exc.tool,
            },
            "agent_id": config.get_agent_id(),
        }
    return {
        "ok": False,
        "error": {
            "type": "bus_api_error",
            "message": str(exc),
            "tool": exc.tool,
            "status_code": exc.status_code,
        },
        "agent_id": config.get_agent_id(),
    }


def _ok(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Success envelope. `agent_id` is merged LAST and deliberately: it is the
    identity THIS process used, and a bus response field of the same name must
    not be able to overwrite the answer to "what was I attributed as"."""
    return {"ok": True, **result, **extra, "agent_id": config.get_agent_id()}


def _client_error(err_type: str, tool: str, **fields: Any) -> dict[str, Any]:
    """A refusal this client makes BEFORE any network call -- same
    `{"ok": False, "error": {...}}` shape as a bus error so a caller has one
    branch, not two."""
    return {
        "ok": False,
        "error": {"type": err_type, "tool": tool, **fields},
        "agent_id": config.get_agent_id(),
    }


@config.gated_write
def post_message(
    topic: str,
    sender: str,
    body: str,
    action_flag: bool = False,
    thread_id: int | None = None,
    reply_to: int | None = None,
    recipient: str | None = None,
) -> dict[str, Any]:
    """Append one message to the bus blackboard (append-only). v1 stores
    action_flag but performs no action -- it is display-only, seen by a human
    watching the command-center panel.

    v0.2.0 adds the three addressing fields `BusMessage` has accepted since
    M1b and this client used to drop on the floor: `thread_id` (the message is
    a reply in that thread), `reply_to` (the message id it answers), and
    `recipient` (a stable ROLE like 'orchestrator' or 'operator'; omitted =
    broadcast). ALL THREE ARE OMITTED FROM THE BODY when None, so a caller
    that does not use them sends exactly the payload v0.1.1 sent.

    The bus VALIDATES thread_id/reply_to against real rows when its thread
    flag is armed -- a reply naming a thread that does not exist, or one
    already resolved, comes back 422, not a silent accept."""
    payload = _compact({
        "topic": topic,
        "sender": sender,
        "body": body,
        "action_flag": action_flag,
        "thread_id": thread_id,
        "reply_to": reply_to,
        "recipient": recipient,
    })
    try:
        result = client.post("post_message", "/message", json=payload)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


def read_messages(
    topic: str | None = None,
    limit: int = 50,
    thread_id: int | None = None,
    recipient: str | None = None,
    since_id: int | None = None,
) -> dict[str, Any]:
    """Recent bus messages, newest first, optionally filtered by topic.

    LIMITATION, TRUE AS OF 2026-09-10 AND STATED HERE BECAUSE A SILENT
    NO-OP IS WORSE THAN A REFUSAL: `thread_id`, `recipient` and `since_id`
    are sent as query params, and TODAY'S BACKEND IGNORES ALL THREE.
    `GET /api/bus/messages` filters on `topic` and `limit` only
    (coordination_bus.py get_messages), and FastAPI drops query params a
    route does not declare. So passing them right now changes NOTHING about
    what comes back -- you get the same topic-filtered page you would
    otherwise, and you must filter client-side (which is what `get_thread`
    does).

    They are wired anyway, deliberately: the server-side filters are a
    separate, already-specced backend change, and when it lands these
    params start working with no change here and no version negotiation. A
    caller that needs to know WHICH behaviour it got should check whether
    the returned rows actually satisfy the filter rather than assume."""
    params: dict[str, Any] = {"limit": limit}
    if topic is not None:
        params["topic"] = topic
    if thread_id is not None:
        params["thread_id"] = thread_id
    if recipient is not None:
        params["recipient"] = recipient
    if since_id is not None:
        params["since_id"] = since_id
    try:
        result = client.get("read_messages", "/messages", params=params)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def claim_lane(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict[str, Any]:
    """Claim a coordination lane before starting work in it: claim-if-free,
    steal-if-lease-expired, renew-if-you-already-own-it. A 409 (lane held
    live by another owner) surfaces as a clean ok=False conflict, not a
    crash -- check `error.type == "bus_api_error"` and `status_code == 409`."""
    if not _valid_lane(lane):
        return _invalid_lane_payload(lane, "claim_lane")
    try:
        result = client.post(
            "claim_lane", f"/lanes/{lane}/claim", json={"owner": owner, "lease_s": lease_s}
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def release_lane(lane: str, owner: str) -> dict[str, Any]:
    """Release a lane you hold. A 409 (held live by another owner) surfaces
    as a clean ok=False conflict, not a crash."""
    if not _valid_lane(lane):
        return _invalid_lane_payload(lane, "release_lane")
    try:
        result = client.post("release_lane", f"/lanes/{lane}/release", json={"owner": owner})
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def heartbeat_lane(lane: str, owner: str, lease_s: int = config.DEFAULT_LEASE_S) -> dict[str, Any]:
    """Renew the lease on a lane you hold live. A 409 (not held live by you)
    surfaces as a clean ok=False conflict telling you to (re)claim instead."""
    if not _valid_lane(lane):
        return _invalid_lane_payload(lane, "heartbeat_lane")
    try:
        result = client.post(
            "heartbeat_lane", f"/lanes/{lane}/heartbeat", json={"owner": owner, "lease_s": lease_s}
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


def get_bus_status() -> dict[str, Any]:
    """Roll-up for the command-center panel: active lanes, orphaned/stale
    claims, recent messages, pending display-only action flags."""
    try:
        result = client.get("get_bus_status", "/status")
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


# ===========================================================================
# v0.2.0 -- threads, validations, dispatches, board, worker, events
#
# Every function below was written against the LIVE Pydantic request model in
# alphahive/backend/coordination_bus.py, not against the route's docstring:
# field names match the model exactly, and a field the model does not have is
# never sent (a model with extra="forbid" 422s on it; a model without it
# silently DROPS the field, which is worse). Where a parameter in this
# client's surface has no home on the wire today, it is refused loudly or
# satisfied CLIENT-SIDE and said so -- never quietly discarded.
# ===========================================================================

# `dispatch_id` is interpolated into a URL path, same hazard as `lane`. The
# bus itself pins the shape (`_DISPATCH_ID_PARAM`, 12 lowercase hex), so
# checking it here costs one regex and turns a path-injection attempt into a
# typed refusal instead of a request.
_DISPATCH_ID_RE = re.compile(r"^[0-9a-f]{12}$")
# `task_id` likewise -- mirrors the bus's own `_TASK_ID_PARAM` pattern.
_TASK_ID_RE = re.compile(r"^[\w./\-:]+$")
# The role the bus authenticates with a SECOND secret this MCP does not and
# must not hold.
_OPERATOR_ROLE = "operator"

_SUBJECT_PREFIXES = {"message:": "message", "task:": "task", "proposal:": "proposal"}

# How many rows a client-side composition scans when the backend cannot give
# the answer in one call yet (see get_thread). 500 is the bus's own
# per-request ceiling, so this is "as much as one call can see", not a number
# picked here.
_SCAN_LIMIT = 500


def _resolve_agent(value: str | None) -> str:
    return value if value else config.get_agent_id()


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop None-valued keys so an omitted optional is ABSENT from the JSON
    body, not present-as-null. Matters on the bus's extra="forbid" models,
    where the wire shape is part of the contract, and it keeps the pre-v0.2.0
    tools' payloads byte-identical to what they sent before."""
    return {k: v for k, v in payload.items() if v is not None}


# --- threads ---------------------------------------------------------------

def list_threads(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Newest-first thread list (GET /threads, ungated). `status` is one of
    open/resolved/archived -- the bus types it as a Literal, so an unknown
    value comes back a 422 rather than a silently-empty list.

    NOTE the bus's own default: with `status` omitted, ARCHIVED THREADS ARE
    EXCLUDED. There is no single call that returns everything; ask for
    status="archived" separately."""
    params: dict[str, Any] = {"limit": limit}
    if status is not None:
        params["status"] = status
    try:
        result = client.get("list_threads", "/threads", params=params)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


def _find_thread(
    tool: str, thread_id: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Locate one thread row by id. Returns (thread, error_payload).

    THERE IS NO GET /threads/{id} ROUTE on the bus as of 2026-09-10 (verified
    against the router inventory in coordination_bus.py), so this scans the
    list projection: the default bucket first, then `archived`, which the
    default deliberately excludes. The blind spot is stated rather than
    hidden -- a thread that has fallen more than _SCAN_LIMIT rows down its own
    bucket comes back `thread_not_visible`, NOT as absent."""
    for status in (None, "archived"):
        listing = list_threads(status=status, limit=_SCAN_LIMIT)
        if not listing.get("ok"):
            return None, listing
        for thread in listing.get("threads") or []:
            if thread.get("id") == thread_id:
                return thread, None
    return None, _client_error(
        "thread_not_visible",
        tool,
        thread_id=thread_id,
        reason=(
            f"no thread with id {thread_id} in the newest {_SCAN_LIMIT} rows of "
            "either the live or the archived bucket. The bus has no by-id "
            "thread route yet, so this means 'not visible in the scan window', "
            "which is not the same as 'does not exist'."
        ),
    )


def get_thread(thread_id: int, limit: int = _SCAN_LIMIT) -> dict[str, Any]:
    """One thread plus its messages, oldest-first.

    COMPOSED, NOT FETCHED -- and a caller must know it. The bus has no
    GET /threads/{id} route and its GET /messages filters by `topic` only, so
    this tool (1) finds the thread row in GET /threads, (2) reads
    GET /messages?topic=<the thread's topic> and keeps the rows whose
    `thread_id` matches. The filtering is CLIENT-SIDE.

    The consequence is returned as data rather than left to be discovered:
    `scanned` is how many topic messages were examined and `scan_truncated`
    is True when that hit the request ceiling -- with it True, older replies
    in this thread exist that this call did not see. When the backend gains a
    real by-id thread route (or a `thread_id` filter on GET /messages), this
    composition becomes redundant and those fields go with it."""
    thread, err = _find_thread("get_thread", thread_id)
    if err is not None:
        return err
    listing = read_messages(topic=thread.get("topic"), limit=limit)
    if not listing.get("ok"):
        return listing
    scanned = listing.get("messages") or []
    replies = [m for m in scanned if m.get("thread_id") == thread_id]
    replies.sort(key=lambda m: m.get("id") or 0)
    return _ok(
        {"thread": thread, "messages": replies},
        message_count=len(replies),
        scanned=len(scanned),
        scan_truncated=len(scanned) >= limit,
        composed_client_side=True,
    )


@config.gated_write
def open_thread(
    topic: str,
    title: str,
    body: str,
    kind: str | None = None,
    opened_by: str | None = None,
    recipient: str | None = None,
    action_flag: bool = False,
) -> dict[str, Any]:
    """Open a conversation thread and its root message in one call
    (POST /threads, write-secret gated).

    `opened_by` defaults to this process's agent id and IS THE RESOLVE
    AUTHORITY afterwards -- the bus lets the opener resolve its own thread, so
    a thread opened under the default can only be resolved by a session
    asserting that same string. `kind` is free text with ONE special value:
    DECIDE marks a thread only the operator may resolve. `action_flag=True`
    is the phone-paging semantic, reserved for genuinely blocking work, and
    defaults False deliberately.

    ThreadOpenRequest sets extra="forbid", so a typo'd key is a 422 you cannot
    miss rather than a silently-dropped field."""
    payload = _compact({
        "topic": topic,
        "title": title,
        "opened_by": _resolve_agent(opened_by),
        "body": body,
        "kind": kind,
        "recipient": recipient,
        "action_flag": action_flag,
    })
    try:
        result = client.post("open_thread", "/threads", json=payload)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def reply_in_thread(
    thread_id: int,
    body: str,
    reply_to: int | None = None,
    sender: str | None = None,
    action_flag: bool = False,
    topic: str | None = None,
    recipient: str | None = None,
) -> dict[str, Any]:
    """Reply inside a thread. A reply IS an ordinary message carrying
    `thread_id` (plus optionally `reply_to`), so this posts to POST /message.

    `topic` IS REQUIRED BY THE BUS (BusMessage.topic, min_length=1) and is not
    part of a thread reply's natural vocabulary, so when the caller omits it
    this tool LOOKS THE THREAD UP and uses the thread's own topic -- one extra
    GET, and the reason the `topic` override parameter exists at all (pass it
    to skip the lookup, or to reply to a thread that has fallen out of the
    scan window). `reply_to`, when omitted, is left unset rather than guessed:
    it means "which message this answers", and the thread's root id is a guess
    this client has no business making silently."""
    resolved_topic = topic
    if resolved_topic is None:
        thread, err = _find_thread("reply_in_thread", thread_id)
        if err is not None:
            return err
        resolved_topic = thread.get("topic")
    return post_message(
        resolved_topic,
        _resolve_agent(sender),
        body,
        action_flag=action_flag,
        thread_id=thread_id,
        reply_to=reply_to,
        recipient=recipient,
    )


@config.gated_write
def resolve_thread(
    thread_id: int, resolved_by: str | None = None, note: str | None = None
) -> dict[str, Any]:
    """Resolve a thread you opened (POST /threads/{id}/resolve).

    REFUSED CLIENT-SIDE: resolved_by="operator", in any casing. The bus proves
    the operator role with an X-Bus-Operator-Secret that this MCP server does
    not hold and must never hold -- so the request could only ever come back
    403, and asking is how a forged-provenance attempt starts. Operator
    resolution stays a CLI/paste action.

    `note`: ThreadResolveRequest has exactly ONE field, `resolved_by`, and does
    NOT set extra="forbid" -- a note sent on that body would be accepted and
    silently dropped. So a note is POSTED AS A REPLY IN THE THREAD FIRST and
    only then is the thread resolved; `note_posted` in the result says whether
    that happened. If the reply fails the thread is left OPEN and the error is
    returned: a note that vanished and a thread closed without its stated
    reason is the failure mode this ordering avoids."""
    who = _resolve_agent(resolved_by)
    if who.strip().lower() == _OPERATOR_ROLE:
        return _client_error(
            "operator_resolution_refused",
            "resolve_thread",
            thread_id=thread_id,
            reason=(
                "resolved_by='operator' requires the X-Bus-Operator-Secret, "
                "which this MCP server deliberately does not hold. Operator "
                "resolution is a CLI/paste action, not a tool call."
            ),
        )
    note_posted = False
    if note is not None:
        reply = reply_in_thread(thread_id, note, sender=who)
        if not reply.get("ok"):
            return reply
        note_posted = True
    try:
        result = client.post(
            "resolve_thread",
            f"/threads/{int(thread_id)}/resolve",
            json={"resolved_by": who},
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result, note_posted=note_posted)
