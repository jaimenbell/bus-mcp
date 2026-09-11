"""respx-mocked tests of bus_mcp.client -- the typed httpx wrapper. Covers
the success path plus all three failure taxonomies: connection-refused
(BusUnreachable), 4xx/5xx (BusApiError), and non-JSON body (BusApiError).
No live bus involved anywhere in this file.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from bus_mcp import client

BASE = "http://127.0.0.1:8100/api/bus"


@respx.mock
def test_get_success_returns_parsed_json():
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={"ok": True}))
    result = client.get("get_bus_status", "/status")
    assert result == {"ok": True}


@respx.mock
def test_post_success_returns_parsed_json():
    respx.post(f"{BASE}/message").mock(
        return_value=httpx.Response(200, json={"id": 1, "topic": "t"})
    )
    result = client.post("post_message", "/message", json={"topic": "t"})
    assert result == {"id": 1, "topic": "t"}


@respx.mock
def test_get_with_params_forwards_query_string():
    route = respx.get(f"{BASE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    client.get("read_messages", "/messages", params={"topic": "converge", "limit": 20})
    assert route.calls.last.request.url.params["topic"] == "converge"
    assert route.calls.last.request.url.params["limit"] == "20"


@respx.mock
def test_connection_refused_raises_bus_unreachable():
    respx.get(f"{BASE}/status").mock(side_effect=httpx.ConnectError("Connection refused"))
    with pytest.raises(client.BusUnreachable) as exc_info:
        client.get("get_bus_status", "/status")
    assert "get_bus_status" in str(exc_info.value)
    assert "coordination bus isn't reachable" in str(exc_info.value)
    assert exc_info.value.tool == "get_bus_status"


@respx.mock
def test_timeout_raises_bus_unreachable():
    respx.get(f"{BASE}/status").mock(side_effect=httpx.TimeoutException("timed out"))
    with pytest.raises(client.BusUnreachable):
        client.get("get_bus_status", "/status")


@pytest.mark.no_respx
def test_invalid_url_raises_bus_unreachable(monkeypatch):
    monkeypatch.setenv("BUS_MCP_BASE_URL", "not-a-valid-url-scheme")
    with pytest.raises(client.BusUnreachable):
        client.get("get_bus_status", "/status")


@respx.mock
def test_409_conflict_raises_bus_api_error_with_detail():
    respx.post(f"{BASE}/lanes/feeds/claim").mock(
        return_value=httpx.Response(409, json={"detail": "lane 'feeds' held by 'other'"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.post("claim_lane", "/lanes/feeds/claim", json={"owner": "me"})
    assert exc_info.value.status_code == 409
    assert "held by 'other'" in str(exc_info.value)


@respx.mock
def test_422_validation_error_raises_bus_api_error():
    respx.post(f"{BASE}/message").mock(
        return_value=httpx.Response(422, json={"detail": "topic too short"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.post("post_message", "/message", json={"topic": ""})
    assert exc_info.value.status_code == 422


@respx.mock
def test_500_server_error_raises_bus_api_error():
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(client.BusApiError) as exc_info:
        client.get("get_bus_status", "/status")
    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value)


@respx.mock
def test_error_response_non_json_body_falls_back_to_text():
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(503, text="service unavailable", headers={"content-type": "text/plain"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.get("get_bus_status", "/status")
    assert "service unavailable" in str(exc_info.value)


@respx.mock
def test_success_non_json_body_raises_bus_api_error():
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(200, text="not json", headers={"content-type": "text/plain"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.get("get_bus_status", "/status")
    assert "non-JSON" in str(exc_info.value)


@respx.mock
def test_empty_body_success_returns_empty_dict():
    respx.post(f"{BASE}/lanes/feeds/release").mock(return_value=httpx.Response(204))
    result = client.post("release_lane", "/lanes/feeds/release", json={"owner": "me"})
    assert result == {}


# ── v1.1 write-secret header (BUS_WRITE_SECRET -> X-Bus-Secret) ─────────────────
# Mirrors the coordination-bus v1.1 write-auth: unset -> no header sent (open,
# byte-identical to pre-v1.1); set -> every client.post() attaches the header
# automatically, so routes.py callers need zero awareness of arming state.

@respx.mock
def test_post_sends_no_secret_header_when_unset(monkeypatch):
    monkeypatch.delenv("BUS_WRITE_SECRET", raising=False)
    route = respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1}))
    client.post("post_message", "/message", json={"topic": "t"})
    assert "x-bus-secret" not in route.calls.last.request.headers


@respx.mock
def test_post_sends_secret_header_when_set(monkeypatch):
    monkeypatch.setenv("BUS_WRITE_SECRET", "s3cr3t")
    route = respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1}))
    client.post("post_message", "/message", json={"topic": "t"})
    assert route.calls.last.request.headers["x-bus-secret"] == "s3cr3t"


@respx.mock
def test_post_secret_header_matches_env_value_exactly(monkeypatch):
    monkeypatch.setenv("BUS_WRITE_SECRET", "another-value-123")
    route = respx.post(f"{BASE}/lanes/feeds/claim").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client.post("claim_lane", "/lanes/feeds/claim", json={"owner": "me"})
    assert route.calls.last.request.headers["x-bus-secret"] == "another-value-123"


@respx.mock
def test_post_401_when_armed_and_bus_rejects_raises_bus_api_error(monkeypatch):
    # Proves the 401 the real armed bus would return on a bad/absent secret
    # surfaces through the same typed BusApiError path as any other 4xx --
    # no special-casing needed in client.py for the auth failure mode.
    monkeypatch.setenv("BUS_WRITE_SECRET", "wrong-secret")
    respx.post(f"{BASE}/message").mock(
        return_value=httpx.Response(401, json={"detail": "missing or invalid X-Bus-Secret"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.post("post_message", "/message", json={"topic": "t"})
    assert exc_info.value.status_code == 401
    assert "X-Bus-Secret" in str(exc_info.value)


@respx.mock
def test_get_never_sends_secret_header_even_when_armed(monkeypatch):
    # GET routes are never gated bus-side; the client mirrors that by never
    # attaching the header to client.get() regardless of BUS_WRITE_SECRET.
    monkeypatch.setenv("BUS_WRITE_SECRET", "s3cr3t")
    route = respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.get("get_bus_status", "/status")
    assert "x-bus-secret" not in route.calls.last.request.headers


# -- BUS_MACHINE_TOKEN (X-Bus-Token) + precedence over the legacy secret --------
# Mirrors alphahive backend/auth/dependency.py::resolve_principal, which checks
# a presented machine_token BEFORE it ever looks at legacy_secret and returns
# immediately on that branch -- so this client sends X-Bus-Token ALONE when a
# token is configured, never alongside X-Bus-Secret, even if a secret is also
# set. See client.post()'s own docstring for the full precedence rationale.

@respx.mock
def test_post_sends_no_auth_header_when_neither_configured(monkeypatch):
    monkeypatch.delenv("BUS_MACHINE_TOKEN", raising=False)
    monkeypatch.delenv("BUS_WRITE_SECRET", raising=False)
    route = respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1}))
    client.post("post_message", "/message", json={"topic": "t"})
    headers = route.calls.last.request.headers
    assert "x-bus-token" not in headers
    assert "x-bus-secret" not in headers


@respx.mock
def test_post_sends_token_header_when_token_only_configured(monkeypatch):
    monkeypatch.setenv("BUS_MACHINE_TOKEN", "tok_id.tok_secret")
    monkeypatch.delenv("BUS_WRITE_SECRET", raising=False)
    route = respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1}))
    client.post("post_message", "/message", json={"topic": "t"})
    headers = route.calls.last.request.headers
    assert headers["x-bus-token"] == "tok_id.tok_secret"
    assert "x-bus-secret" not in headers


@respx.mock
def test_post_sends_secret_header_when_secret_only_configured(monkeypatch):
    monkeypatch.delenv("BUS_MACHINE_TOKEN", raising=False)
    monkeypatch.setenv("BUS_WRITE_SECRET", "s3cr3t")
    route = respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1}))
    client.post("post_message", "/message", json={"topic": "t"})
    headers = route.calls.last.request.headers
    assert headers["x-bus-secret"] == "s3cr3t"
    assert "x-bus-token" not in headers


@respx.mock
def test_post_token_wins_over_secret_when_both_configured(monkeypatch):
    # The precedence case: BOTH env vars set. The token must win and the
    # secret must NOT be sent alongside it -- mirroring the server, which
    # would ignore the secret entirely once a machine_token is presented.
    monkeypatch.setenv("BUS_MACHINE_TOKEN", "tok_id.tok_secret")
    monkeypatch.setenv("BUS_WRITE_SECRET", "s3cr3t")
    route = respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1}))
    client.post("post_message", "/message", json={"topic": "t"})
    headers = route.calls.last.request.headers
    assert headers["x-bus-token"] == "tok_id.tok_secret"
    assert "x-bus-secret" not in headers


@respx.mock
def test_get_never_sends_token_header_even_when_armed(monkeypatch):
    # GET routes are never gated bus-side; client.get() ignores BUS_MACHINE_TOKEN
    # exactly like it already ignores BUS_WRITE_SECRET.
    monkeypatch.setenv("BUS_MACHINE_TOKEN", "tok_id.tok_secret")
    route = respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={"ok": True}))
    client.get("get_bus_status", "/status")
    assert "x-bus-token" not in route.calls.last.request.headers


@respx.mock
def test_post_401_with_bad_token_raises_bus_api_error(monkeypatch):
    # Same typed-error path as the legacy-secret 401 case above -- no
    # special-casing needed in client.py for the token auth-failure mode.
    monkeypatch.setenv("BUS_MACHINE_TOKEN", "tok_id.wrong-token-secret")
    respx.post(f"{BASE}/message").mock(
        return_value=httpx.Response(401, json={"detail": "missing or invalid credentials"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.post("post_message", "/message", json={"topic": "t"})
    assert exc_info.value.status_code == 401


@respx.mock
def test_machine_token_value_never_appears_in_bus_api_error_text(monkeypatch):
    """Redaction: the bus's own auth failures return a generic, non-disclosing
    detail string (dependency.py's `_DENIED_DETAIL` -- byte-identical for
    every rejection reason), so a correctly-behaving server never echoes the
    token back. This pins that OUR side doesn't invent a way to leak it
    either -- the token must not appear anywhere in the raised exception's
    text, which is the only place a caller-visible string is built from this
    request."""
    token = "tok_id.super-secret-token-value-must-not-leak"
    monkeypatch.setenv("BUS_MACHINE_TOKEN", token)
    respx.post(f"{BASE}/message").mock(
        return_value=httpx.Response(401, json={"detail": "missing or invalid credentials"})
    )
    with pytest.raises(client.BusApiError) as exc_info:
        client.post("post_message", "/message", json={"topic": "t"})
    assert token not in str(exc_info.value)
    assert token not in repr(exc_info.value)


@respx.mock
def test_machine_token_value_never_appears_in_success_result(monkeypatch):
    """Redaction, success-path half: a 200 response body containing the
    request's own headers echoed back (e.g. a debug echo route) must not
    make it into the parsed result untouched -- pinned here by asserting the
    real, non-echoing route's result is exactly the mocked body, i.e.
    client.post() itself adds nothing containing the token to the result."""
    token = "tok_id.another-secret-must-not-leak"
    monkeypatch.setenv("BUS_MACHINE_TOKEN", token)
    respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 1, "topic": "t"}))
    result = client.post("post_message", "/message", json={"topic": "t"})
    assert token not in repr(result)
