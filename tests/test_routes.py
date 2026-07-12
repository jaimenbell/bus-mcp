"""respx-mocked tests of bus_mcp.routes -- the normalization layer that
catches client.BusUnreachable / client.BusApiError and turns them into a
clean `{"ok": False, "error": {...}}` dict. Proves the "never a raw crash"
acceptance criterion at the layer server.py tools actually call.
"""
from __future__ import annotations

import httpx
import respx

from bus_mcp import routes

BASE = "http://127.0.0.1:8100/api/bus"


@respx.mock
def test_post_message_success():
    respx.post(f"{BASE}/message").mock(
        return_value=httpx.Response(
            200, json={"id": 1, "topic": "converge", "sender": "a", "body": "hi", "action_flag": False, "ts": "t"}
        )
    )
    result = routes.post_message("converge", "a", "hi")
    assert result["ok"] is True
    assert result["id"] == 1
    assert result["topic"] == "converge"


@respx.mock
def test_read_messages_success_with_topic_filter():
    route = respx.get(f"{BASE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": 1}]})
    )
    result = routes.read_messages(topic="converge", limit=10)
    assert result["ok"] is True
    assert result["messages"] == [{"id": 1}]
    assert route.calls.last.request.url.params["topic"] == "converge"


@respx.mock
def test_read_messages_success_no_topic_filter_omits_param():
    route = respx.get(f"{BASE}/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    routes.read_messages()
    assert "topic" not in route.calls.last.request.url.params


@respx.mock
def test_claim_lane_success():
    respx.post(f"{BASE}/lanes/feeds/claim").mock(
        return_value=httpx.Response(200, json={"ok": True, "decision": "claim", "lane": {"lane": "feeds"}})
    )
    result = routes.claim_lane("feeds", "session-A")
    assert result["ok"] is True
    assert result["decision"] == "claim"


@respx.mock
def test_claim_lane_success_passes_through_lease_s():
    # coordination-bus v1.3+: claim responses echo the EFFECTIVE (post-clamp)
    # lease_s granted. routes.py does a generic {"ok": True, **result} merge,
    # so this new field flows through with zero routes.py code changes --
    # this test locks that passthrough behavior in place.
    respx.post(f"{BASE}/lanes/feeds/claim").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "decision": "claim", "lane": {"lane": "feeds"}, "lease_s": 3600}
        )
    )
    result = routes.claim_lane("feeds", "session-A", lease_s=7200)
    assert result["ok"] is True
    assert result["lease_s"] == 3600


@respx.mock
def test_claim_lane_success_without_lease_s_omits_gracefully():
    # Pre-v1.3 bus servers don't return lease_s at all. Must not KeyError or
    # synthesize a value -- the field is simply absent, version-tolerant.
    respx.post(f"{BASE}/lanes/feeds/claim").mock(
        return_value=httpx.Response(200, json={"ok": True, "decision": "claim", "lane": {"lane": "feeds"}})
    )
    result = routes.claim_lane("feeds", "session-A")
    assert result["ok"] is True
    assert "lease_s" not in result


@respx.mock
def test_claim_lane_conflict_normalizes_to_ok_false():
    respx.post(f"{BASE}/lanes/feeds/claim").mock(
        return_value=httpx.Response(409, json={"detail": "lane 'feeds' held by 'other' (lease 120s remaining)"})
    )
    result = routes.claim_lane("feeds", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_api_error"
    assert result["error"]["status_code"] == 409
    assert "held by 'other'" in result["error"]["message"]


@respx.mock
def test_release_lane_success():
    respx.post(f"{BASE}/lanes/feeds/release").mock(
        return_value=httpx.Response(200, json={"ok": True, "decision": "released"})
    )
    result = routes.release_lane("feeds", "session-A")
    assert result["ok"] is True
    assert result["decision"] == "released"


@respx.mock
def test_release_lane_conflict_normalizes_to_ok_false():
    respx.post(f"{BASE}/lanes/feeds/release").mock(
        return_value=httpx.Response(409, json={"detail": "lane 'feeds' held live by another owner"})
    )
    result = routes.release_lane("feeds", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_api_error"
    assert result["error"]["status_code"] == 409


@respx.mock
def test_heartbeat_lane_success():
    respx.post(f"{BASE}/lanes/feeds/heartbeat").mock(
        return_value=httpx.Response(200, json={"ok": True, "decision": "heartbeat"})
    )
    result = routes.heartbeat_lane("feeds", "session-A")
    assert result["ok"] is True
    assert result["decision"] == "heartbeat"


@respx.mock
def test_heartbeat_lane_success_passes_through_lease_s():
    # Same v1.3+ passthrough as claim_lane -- renewal also echoes the
    # effective (post-clamp) lease_s.
    respx.post(f"{BASE}/lanes/feeds/heartbeat").mock(
        return_value=httpx.Response(200, json={"ok": True, "decision": "heartbeat", "lease_s": 1800})
    )
    result = routes.heartbeat_lane("feeds", "session-A", lease_s=3600)
    assert result["ok"] is True
    assert result["lease_s"] == 1800


@respx.mock
def test_heartbeat_lane_success_without_lease_s_omits_gracefully():
    respx.post(f"{BASE}/lanes/feeds/heartbeat").mock(
        return_value=httpx.Response(200, json={"ok": True, "decision": "heartbeat"})
    )
    result = routes.heartbeat_lane("feeds", "session-A")
    assert result["ok"] is True
    assert "lease_s" not in result


@respx.mock
def test_heartbeat_lane_conflict_normalizes_to_ok_false():
    respx.post(f"{BASE}/lanes/feeds/heartbeat").mock(
        return_value=httpx.Response(409, json={"detail": "lane 'feeds' not held live by 'session-A' -- claim it instead"})
    )
    result = routes.heartbeat_lane("feeds", "session-A")
    assert result["ok"] is False
    assert result["error"]["status_code"] == 409
    assert "claim it instead" in result["error"]["message"]


@respx.mock
def test_get_bus_status_success():
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "active_lanes": [],
                "orphaned_claims": [],
                "recent_messages": [],
                "_meta": {"active_lane_count": 0, "arms_nothing": True},
            },
        )
    )
    result = routes.get_bus_status()
    assert result["ok"] is True
    assert result["_meta"]["arms_nothing"] is True


@respx.mock
def test_get_bus_status_passes_through_max_lease_seconds():
    # coordination-bus v1.3+: GET /status exposes the configured lease
    # ceiling in _meta.max_lease_seconds. Generic {"ok": True, **result}
    # merge in routes.py already surfaces this -- test locks it in.
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "active_lanes": [],
                "orphaned_claims": [],
                "recent_messages": [],
                "_meta": {"active_lane_count": 0, "arms_nothing": True, "max_lease_seconds": 3600},
            },
        )
    )
    result = routes.get_bus_status()
    assert result["ok"] is True
    assert result["_meta"]["max_lease_seconds"] == 3600


@respx.mock
def test_get_bus_status_without_max_lease_seconds_omits_gracefully():
    # Pre-v1.3 bus servers don't include max_lease_seconds in _meta at all.
    respx.get(f"{BASE}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "active_lanes": [],
                "orphaned_claims": [],
                "recent_messages": [],
                "_meta": {"active_lane_count": 0, "arms_nothing": True},
            },
        )
    )
    result = routes.get_bus_status()
    assert result["ok"] is True
    assert "max_lease_seconds" not in result["_meta"]


@respx.mock
def test_get_bus_status_unreachable_normalizes_to_ok_false():
    respx.get(f"{BASE}/status").mock(side_effect=httpx.ConnectError("refused"))
    result = routes.get_bus_status()
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_unreachable"
    assert "coordination bus isn't reachable" in result["error"]["message"]
    assert result["error"]["tool"] == "get_bus_status"


@respx.mock
def test_post_message_unreachable_normalizes_to_ok_false():
    respx.post(f"{BASE}/message").mock(side_effect=httpx.ConnectError("refused"))
    result = routes.post_message("t", "s", "b")
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_unreachable"


@respx.mock
def test_read_messages_unreachable_normalizes_to_ok_false():
    respx.get(f"{BASE}/messages").mock(side_effect=httpx.ConnectError("refused"))
    result = routes.read_messages()
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_unreachable"


@respx.mock
def test_claim_lane_unreachable_normalizes_to_ok_false():
    respx.post(f"{BASE}/lanes/feeds/claim").mock(side_effect=httpx.ConnectError("refused"))
    result = routes.claim_lane("feeds", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_unreachable"


@respx.mock
def test_release_lane_unreachable_normalizes_to_ok_false():
    respx.post(f"{BASE}/lanes/feeds/release").mock(side_effect=httpx.ConnectError("refused"))
    result = routes.release_lane("feeds", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_unreachable"


@respx.mock
def test_heartbeat_lane_unreachable_normalizes_to_ok_false():
    respx.post(f"{BASE}/lanes/feeds/heartbeat").mock(side_effect=httpx.ConnectError("refused"))
    result = routes.heartbeat_lane("feeds", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "bus_unreachable"


# --- Security: lane path-injection validation (P3) ---


def test_claim_lane_rejects_path_injection_without_http_call():
    """A lane with '/' would manipulate the URL path — must be rejected before
    any HTTP call, returning the standard ok=False error dict (never raises)."""
    result = routes.claim_lane("feeds/../status", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "invalid_lane"
    assert result["error"]["tool"] == "claim_lane"


def test_release_lane_rejects_invalid_lane():
    result = routes.release_lane("a?b=c", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "invalid_lane"


def test_heartbeat_lane_rejects_invalid_lane():
    result = routes.heartbeat_lane("../../admin", "session-A")
    assert result["ok"] is False
    assert result["error"]["type"] == "invalid_lane"


def test_valid_lane_charset_still_accepted():
    """Legit lane names (letters/digits/underscore/hyphen) must still pass."""
    from bus_mcp.routes import _valid_lane
    assert _valid_lane("feeds") is True
    assert _valid_lane("lane_1-b") is True
    assert _valid_lane("bad/lane") is False
    assert _valid_lane("") is False
