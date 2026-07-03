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
) -> dict[str, Any]:
    """Make one request against the bus. Raises BusUnreachable on a network
    failure, BusApiError on a 4xx/5xx, else returns the parsed JSON body."""
    try:
        with httpx.Client(timeout=config.get_timeout_s()) as http_client:
            response = http_client.request(method, _url(path), params=params, json=json)
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
    return request(tool, "POST", path, json=json)
