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
import secrets
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


def _valid_task_id(task_id: Any) -> bool:
    """Fail-closed path guard for a value interpolated into the request URL.

    STRICTER THAN THE BUS'S OWN `_TASK_ID_PARAM` CHARSET, deliberately: that
    pattern allows `.` and `/`, so `../../status` SATISFIES IT. The charset
    describes what a task id may contain; it does not exclude a traversal
    built out of those same characters. So `..` is rejected outright, as is a
    leading or trailing separator. No legitimate minted task id contains
    `..`, so this narrows nothing real."""
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id):
        return False
    if ".." in task_id:
        return False
    return not (task_id.startswith("/") or task_id.endswith("/"))


def _invalid_task_id_payload(task_id: Any, tool: str) -> dict[str, Any]:
    return _client_error(
        "invalid_task_id",
        tool,
        task_id=task_id,
        reason=(
            f"task_id must match {_TASK_ID_RE.pattern} (word chars, dot, "
            "slash, hyphen, colon), must not contain '..', and must not start "
            "or end with '/' -- it is interpolated into the request URL"
        ),
    )


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


# --- validations -----------------------------------------------------------
#
# THERE IS DELIBERATELY NO `decide` TOOL, and its absence is pinned by a test
# (tests/test_rails_pins.py -- which names the route path; this file must not,
# so the pin can grep for it without exempting comments). The decide route is
# the one bus route authenticated by the OPERATOR secret, a second secret this
# MCP server does not hold and must never hold. A quorum this client can
# request and vote in, but cannot decide, is the whole point of the
# separation: deciding stays a CLI/paste action the operator performs.


def list_validations(
    limit: int = 50,
    subject_ref: str | None = None,
    verdict: str | None = None,
    thread_id: int | None = None,
) -> dict[str, Any]:
    """Validations, newest first (GET /validations, ungated).

    `verdict` is typed as a Literal server-side (pending/confirmed/refuted/
    indeterminate), so a misspelled filter is a 422 rather than an empty list
    that reads like 'nothing to refute'."""
    params = _compact({
        "limit": limit,
        "subject_ref": subject_ref,
        "verdict": verdict,
        "thread_id": thread_id,
    })
    try:
        result = client.get("list_validations", "/validations", params=params)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


def get_validation(validation_id: int) -> dict[str, Any]:
    """ONE validation by id (GET /validations/{id}, ungated).

    Use this rather than scanning `list_validations`: the list is bounded, and
    a validation older than the page is the exact case where 'no such row'
    would be a lie about an outstanding refutation. A missing row is a 404
    here, so 'absent' and 'you could not see it' stay different answers."""
    try:
        result = client.get("get_validation", f"/validations/{int(validation_id)}")
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def request_validation(
    subject_ref: str,
    evidence_refs: str | None = None,
    requested_by: str | None = None,
    subject_kind: str | None = None,
) -> dict[str, Any]:
    """Open a validation on a subject (POST /validations, write-secret gated).

    `subject_ref` is prefixed: 'message:<id>', 'task:<id>' or
    'proposal:<repo>:<path>'. `subject_kind` is REQUIRED by the bus and is
    DERIVED FROM THAT PREFIX when omitted -- derived, not defaulted, because a
    default would silently mis-tier a subject, and the bus refuses a
    kind/prefix mismatch anyway. A subject_ref with no recognized prefix is
    refused here with the three valid prefixes named, rather than sent to
    collect a less specific 422.

    `tier` IS NOT A PARAMETER, deliberately: the bus derives it from the
    subject and its model forbids the key outright. `evidence_refs` is FREE
    TEXT (a single string on the wire), not a list -- matching
    ValidationRequest exactly."""
    kind = subject_kind
    if kind is None:
        for prefix, derived in _SUBJECT_PREFIXES.items():
            if subject_ref.startswith(prefix):
                kind = derived
                break
    if kind is None:
        return _client_error(
            "unknown_subject_kind",
            "request_validation",
            subject_ref=subject_ref,
            reason=(
                "subject_kind could not be derived from subject_ref. Prefix it "
                "with one of 'message:', 'task:', 'proposal:' or pass "
                "subject_kind explicitly -- guessing a kind here would guess a "
                "tier, and the tier decides who has to sign off."
            ),
        )
    payload = _compact({
        "subject_kind": kind,
        "subject_ref": subject_ref,
        "requested_by": _resolve_agent(requested_by),
        "evidence_refs": evidence_refs,
    })
    try:
        result = client.post("request_validation", "/validations", json=payload)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def vote(
    validation_id: int,
    dispatch_id: str,
    verdict: str,
    evidence: str,
    voter: str | None = None,
) -> dict[str, Any]:
    """Cast one vote on a validation (POST /validations/{id}/vote).

    `dispatch_id` must be a REGISTERED, still-open dispatch (mint one with
    `mint_dispatch`) -- the bus refuses a vote cast under an id nobody minted,
    which is what makes a quorum count something that had to be declared in
    advance. It is shape-checked here first (12 lowercase hex) because it is
    interpolated into nothing, but a malformed id is worth a typed refusal
    rather than a round-trip.

    `verdict` is confirmed / refuted / indeterminate. `evidence` becomes
    `evidence_ref` on the wire -- the bus's field name, a POINTER (a path, a
    URL, a message ref), not the argument itself. A voter cannot confirm its
    own request: the bus answers that 403 `self_confirmation`."""
    if not isinstance(dispatch_id, str) or not _DISPATCH_ID_RE.match(dispatch_id):
        return _client_error(
            "invalid_dispatch_id",
            "vote",
            dispatch_id=dispatch_id,
            reason="dispatch_id must be exactly 12 lowercase hex characters",
        )
    payload = {
        "voter": _resolve_agent(voter),
        "dispatch_id": dispatch_id,
        "verdict": verdict,
        "evidence_ref": evidence,
    }
    try:
        result = client.post(
            "vote", f"/validations/{int(validation_id)}/vote", json=payload
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


# --- dispatches ------------------------------------------------------------

def list_dispatches(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Registered dispatches, newest first (GET /dispatches, ungated).
    `status` is open / reported / expired."""
    params = _compact({"limit": limit, "status": status})
    try:
        result = client.get("list_dispatches", "/dispatches", params=params)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def mint_dispatch(
    lane: str, repo: str, purpose: str, minted_by: str | None = None
) -> dict[str, Any]:
    """Register a dispatch so a vote cast under it can be COUNTED
    (POST /dispatches, write-secret gated). The server mints the 12-hex id.

    The honest limit, restated from the bus's own route: the write secret is
    the only identity here, so a holder can register a dispatch it never ran.
    What this buys is not authentication -- it is that a quorum counts
    something somebody had to declare in advance and can be asked to report
    on. `minted_by` defaults to this server's agent id.

    DispatchMintRequest sets extra="forbid": a typo'd key is a 422, never a
    silently-dropped field."""
    payload = _compact({
        "minted_by": _resolve_agent(minted_by),
        "lane": lane,
        "repo": repo,
        "purpose": purpose,
    })
    try:
        result = client.post("mint_dispatch", "/dispatches", json=payload)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
def report_dispatch(dispatch_id: str, report_ref: str) -> dict[str, Any]:
    """Close the loop on a dispatch: the lane reports, naming its evidence
    (POST /dispatches/{id}/report).

    `dispatch_id` is interpolated into the URL PATH, so it is confined to the
    bus's own 12-lowercase-hex shape before it can get there -- same fail-
    closed reasoning as `lane`. `report_ref` is a POINTER (a report path, a
    commit, a message ref), not the report text."""
    if not isinstance(dispatch_id, str) or not _DISPATCH_ID_RE.match(dispatch_id):
        return _client_error(
            "invalid_dispatch_id",
            "report_dispatch",
            dispatch_id=dispatch_id,
            reason="dispatch_id must be exactly 12 lowercase hex characters",
        )
    try:
        result = client.post(
            "report_dispatch",
            f"/dispatches/{dispatch_id}/report",
            json={"report_ref": report_ref},
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


# --- task board ------------------------------------------------------------
#
# THERE IS DELIBERATELY NO TASK-MINTING TOOL. The bare task-collection POST
# mints executable work and its auto class is authenticated by the OPERATOR
# secret; minting stays a CLI/paste ritual the operator runs against a staged
# file they have read. This client reads the board and -- only when a second
# env gate is explicitly armed -- claims, heartbeats and finishes. The sweep
# route is likewise absent: it is a maintenance operation that terminally
# abandons other claimants' rows. Both paths are named only in
# tests/test_rails_pins.py, which greps this package for them.


def list_tasks_board(
    status: str | None = None, limit: int | None = None, include_archived: bool = False
) -> dict[str, Any]:
    """The task board projection (GET /tasks/board, ungated).

    `status` AND `limit` ARE APPLIED CLIENT-SIDE, and this is not a detail a
    caller can ignore: the bus route takes ONE query param, `include_archived`,
    and returns the whole board. Filtering here is therefore exact (the rows
    were all fetched) but it is not a server-side page -- on a board that has
    grown large the transfer is the full board either way.

    `include_archived` is the real server-side param and is passed through.
    Its default excludes rows the nightly archival sweep has aged out, which
    is the only thing that sweep is for.

    Every row is already narrowed by the bus's own board allowlist:
    claim_token, verify_cmd, spec_path, repo, branch, note and posted_by never
    leave the backend process on this route."""
    params: dict[str, Any] = {}
    if include_archived:
        params["include_archived"] = True
    try:
        result = client.get("list_tasks_board", "/tasks/board", params=params or None)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    tasks = result.get("tasks")
    if not isinstance(tasks, list):
        # The board is disabled or the shape is unfamiliar -- pass it through
        # untouched rather than manufacturing an empty list.
        return _ok(result)
    filtered = [t for t in tasks if status is None or t.get("status") == status]
    total = len(filtered)
    if limit is not None:
        filtered = filtered[:limit]
    return _ok(
        {**result, "tasks": filtered},
        matched=total,
        returned=len(filtered),
        filtered_client_side=bool(status is not None or limit is not None),
    )


# The three task MUTATIONS below carry TWO gates, stacked: the ordinary write
# gate outermost (so a write-disabled server still answers "writes are off",
# the same answer it gives for every other write), and the task-claim gate
# under it. See config.GROUP_TASK_CLAIM for why the second one exists.

@config.gated_write
@config.gated_task_claim
def claim_task(
    task_id: str,
    owner: str | None = None,
    claim_token: str | None = None,
    lease_s: int | None = None,
) -> dict[str, Any]:
    """Claim a pending board task (POST /tasks/{id}/claim). DARK BY DEFAULT --
    refuses unless BUS_MCP_ENABLE_TASK_CLAIM is armed.

    `claim_token` is MINTED BY THE CALLER, not issued by the server: the bus
    stores it as given so every later heartbeat/finish can prove ownership
    without a round-trip to learn what was assigned. Omit it and this tool
    mints a cryptographically random one -- IT IS RETURNED IN THE RESULT AND
    IS NOT RECOVERABLE LATER (the board projection deliberately withholds
    claim_token), so keep it for heartbeat_task/finish_task.

    `lease_s` IS REFUSED, not ignored. The bus's TaskClaimRequest has exactly
    two fields (owner, claim_token) and sets the lease server-side at 900s;
    a lease_s sent on that body would be silently dropped and the caller would
    heartbeat against a schedule nobody agreed to. Omit it.

    A losing race is a value, not an exception: ok=False with
    error.type='already_claimed_or_exhausted'."""
    if lease_s is not None:
        return _client_error(
            "unsupported_parameter",
            "claim_task",
            parameter="lease_s",
            reason=(
                "the task claim route sets the lease server-side (900s) and its "
                "request model has no lease field; a value passed here would be "
                "silently dropped. Omit lease_s and heartbeat before it expires."
            ),
        )
    if not _valid_task_id(task_id):
        return _invalid_task_id_payload(task_id, "claim_task")
    token = claim_token or secrets.token_hex(16)
    try:
        result = client.post(
            "claim_task",
            f"/tasks/{task_id}/claim",
            json={"owner": _resolve_agent(owner), "claim_token": token},
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result, claim_token=token)


@config.gated_write
@config.gated_task_claim
def heartbeat_task(
    task_id: str,
    claim_token: str,
    want_running: bool = False,
    owner: str | None = None,
) -> dict[str, Any]:
    """Renew the lease on a task you hold (POST /tasks/{id}/heartbeat). DARK
    BY DEFAULT -- refuses unless BUS_MCP_ENABLE_TASK_CLAIM is armed.

    Renews only for the live (owner, claim_token) holder -- both are compared,
    so a stale or borrowed token does not renew someone else's lease.
    `want_running=True` additionally performs the ONE legal forward transition
    this endpoint may make, claimed -> running; it maps to the bus's
    `status: "running"` field and is OMITTED entirely when False (there is no
    edge back, and sending a status the caller did not ask for is how one
    appears)."""
    if not _valid_task_id(task_id):
        return _invalid_task_id_payload(task_id, "heartbeat_task")
    payload: dict[str, Any] = {
        "owner": _resolve_agent(owner),
        "claim_token": claim_token,
    }
    if want_running:
        payload["status"] = "running"
    try:
        result = client.post(
            "heartbeat_task", f"/tasks/{task_id}/heartbeat", json=payload
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


@config.gated_write
@config.gated_task_claim
def finish_task(
    task_id: str,
    claim_token: str,
    status: str,
    exit_code: int | None = None,
    verify_passed: bool | None = None,
    note: str | None = None,
    owner: str | None = None,
    result_ref: str | None = None,
) -> dict[str, Any]:
    """Terminal write on a task you hold (POST /tasks/{id}/finish). DARK BY
    DEFAULT -- refuses unless BUS_MCP_ENABLE_TASK_CLAIM is armed.

    `status` is done / failed / needs_operator -- the bus types it as a
    Literal, so anything else is a 422 rather than an unknown terminal state.
    `verify_passed` is a SEPARATE fact from `status` and from `exit_code`: a
    run can exit 0 with its verification unrun, and conflating the three is
    how a green light gets attached to something nobody checked. Leave it None
    when nothing verified rather than passing True."""
    if not _valid_task_id(task_id):
        return _invalid_task_id_payload(task_id, "finish_task")
    payload = _compact({
        "owner": _resolve_agent(owner),
        "claim_token": claim_token,
        "status": status,
        "exit_code": exit_code,
        "verify_passed": verify_passed,
        "result_ref": result_ref,
        "note": note,
    })
    try:
        result = client.post("finish_task", f"/tasks/{task_id}/finish", json=payload)
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


# --- worker + events -------------------------------------------------------

def get_worker_state() -> dict[str, Any]:
    """The overnight task worker's last status heartbeat (GET /worker,
    ungated).

    MISSING IS NEVER ZERO, and the bus is careful about this: the backend does
    not own the worker, so absence means 'cannot see', never 'nothing
    happened'. Unmeasured fields come back null and the envelope carries
    `status` + `age_s` + `as_of` so DATA AGE is data. An `unavailable` answer
    is an honest answer, not an error."""
    try:
        result = client.get("get_worker_state", "/worker")
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)


def read_events(since: int = 0, limit: int = 50) -> dict[str, Any]:
    """Cursor poll over the append-only event log (GET /events, ungated).

    Rows come back ASCENDING by id -- the opposite order to read_messages, and
    deliberately so: this is a cursor feed, not a recent-first view. Pass the
    result's `cursor` as `since` on the next poll. An empty list is not an
    error: it means caught up, or a fresh log. `cursor` is unchanged when
    there were no new rows, so a client backing off during an outage needs no
    special case."""
    try:
        result = client.get(
            "read_events", "/events", params={"since": since, "limit": limit}
        )
    except (client.BusUnreachable, client.BusApiError) as exc:
        return _error_payload(exc)
    return _ok(result)
