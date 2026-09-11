"""bus_mcp.client -- thin, typed httpx wrapper over the coordination-bus HTTP
API (backend/coordination_bus.py in the alphahive repo).

Every bus route is a plain function here returning a parsed JSON dict on
success. Failure NEVER raises a raw httpx/requests exception up to a tool
caller and never lets a stack trace reach the MCP transport -- it raises one
of the two typed exceptions below, which server.py tool wrappers catch and
turn into a clean `{"ok": False, "error": {...}}` payload.

Typed error taxonomy:
  BusUnreachable -- connect failed / timed out / DNS failure / bad URL. The
    bus process (AlphaHive backend on :8100) is not up or not routable.
  BusApiError    -- the bus responded but with a 4xx/5xx (e.g. 409 lane
    conflict, 422 validation). Carries status_code + the bus's detail text.
"""
from __future__ import annotations

from typing import Any

import httpx

from . import config


class BusUnreachable(Exception):
    """The coordination bus is not reachable (connection refused, timeout,
    DNS failure, or a malformed base URL). Typically means the AlphaHive
    backend isn't running, or isn't running with the bus routes loaded."""

    def __init__(self, message: str, *, tool: str) -> None:
        super().__init__(message)
        self.tool = tool


class BusApiError(Exception):
    """The bus responded with an HTTP error status (4xx/5xx). Carries the
    status code and the bus's own detail message (e.g. a 409 lane-conflict
    detail string from coordination_bus.py)."""

    def __init__(self, message: str, *, tool: str, status_code: int) -> None:
        super().__init__(message)
        self.tool = tool
        self.status_code = status_code


def _url(path: str) -> str:
    return f"{config.get_base_url()}{path}"


def _unreachable_message(tool: str) -> str:
    return (
        f"[{tool}] the coordination bus isn't reachable at {config.get_base_url()} "
        "-- is the AlphaHive backend running with the bus routes loaded "
        "(backend/coordination_bus.py mounted on :8100)?"
    )


def _handle_response(tool: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            body = response.json()
            detail = body.get("detail", response.text)
        except (ValueError, AttributeError):
            detail = response.text or f"HTTP {response.status_code}"
        raise BusApiError(
            f"[{tool}] bus returned {response.status_code}: {detail}",
            tool=tool,
            status_code=response.status_code,
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise BusApiError(
            f"[{tool}] bus returned non-JSON content: {exc}",
            tool=tool,
            status_code=response.status_code,
        ) from exc


def request(
    tool: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make one request against the bus. Raises BusUnreachable on a network
    failure, BusApiError on a 4xx/5xx, else returns the parsed JSON body."""
    try:
        with httpx.Client(timeout=config.get_timeout_s()) as http_client:
            response = http_client.request(
                method, _url(path), params=params, json=json, headers=headers
            )
    except httpx.HTTPError as exc:
        raise BusUnreachable(_unreachable_message(tool), tool=tool) from exc
    except httpx.InvalidURL as exc:
        # httpx.InvalidURL does NOT subclass httpx.HTTPError -- must be
        # listed explicitly or a malformed BUS_MCP_BASE_URL crashes instead
        # of surfacing a clean typed error.
        raise BusUnreachable(_unreachable_message(tool), tool=tool) from exc
    return _handle_response(tool, response)


def get(tool: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return request(tool, "GET", path, params=params)


def post(tool: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST to a write route, attaching AT MOST ONE auth header, chosen by a
    precedence that mirrors the coordination-bus's own credential resolution
    (alphahive `backend/auth/dependency.py::resolve_principal`):

      1. BUS_MACHINE_TOKEN set -> send `X-Bus-Token: <token>` ONLY. The
         legacy secret, even if also configured, is NOT sent alongside it.
      2. else BUS_WRITE_SECRET set -> send `X-Bus-Secret: <secret>` (the
         pre-existing v1.1 fallback path, unchanged).
      3. neither set -> no auth header (open, byte-identical to pre-token
         behavior and to an unarmed bus).

    WHY THE PRECEDENCE IS "TOKEN ONLY, NEVER BOTH" -- mirroring the server,
    not inventing a client-side rule. `resolve_principal` checks a presented
    `machine_token` BEFORE it ever looks at `legacy_secret`, and returns
    immediately on that branch:

        if machine_token:
            return machine_tokens.resolve(machine_token, now)
        configured = write_secret._write_secret_provider()
        ...

    -- so if this client sent both headers, the server would evaluate the
    token and ignore the secret entirely regardless. That same function's
    docstring names the "ONE DELIBERATE TIGHTENING" this mirrors: a caller
    who PRESENTS a credential and it fails to resolve is REJECTED outright,
    never silently retried against a lower-precedence one (`dependency.py`
    lines 30-35: "Presenting a credential means 'authenticate me as this';
    silently downgrading a failed credential to 'open' would make the scope
    check bypassable"). Sending `X-Bus-Secret` alongside a bad/expired token
    would misrepresent that as a fallback the server will actually take --
    it won't, so this client doesn't offer the illusion of one.

    Callers (routes.py) never need to know or care which credential, if any,
    is configured or which header this function chose."""
    token = config.get_machine_token()
    if token is not None:
        headers = {"X-Bus-Token": token}
    else:
        secret = config.get_write_secret()
        headers = {"X-Bus-Secret": secret} if secret is not None else None
    return request(tool, "POST", path, json=json, headers=headers)
