"""respx-mocked tests for the v0.2.0 thread tools plus the three addressing
fields post_message/read_messages gained.

Two things these pin that a passthrough test normally would not:

1. THE REQUEST SHAPE, not just the response. `ThreadOpenRequest` sets
   extra="forbid" and `ThreadResolveRequest` does not, so what this client
   puts on the wire is the contract -- an extra key 422s on one model and is
   silently swallowed by the other.
2. THE CLIENT-SIDE COMPOSITIONS. `get_thread` and `reply_in_thread` answer
   questions today's backend cannot answer in one call. Tests assert the
   composition (which calls, in which order, with which params) because that
   is the behaviour that must keep working -- and must be recognisable as
   redundant -- once the server-side routes land.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from bus_mcp import routes

BASE = "http://127.0.0.1:8100/api/bus"


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# post_message -- the three addressing fields
# ---------------------------------------------------------------------------

class TestPostMessageAddressing:
    @respx.mock
    def test_thread_fields_are_sent_when_given(self):
        route = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 9})
        )
        result = routes.post_message(
            "wave", "lane:a", "hi", thread_id=2, reply_to=24047, recipient="orchestrator"
        )
        assert result["ok"] is True
        assert _sent(route) == {
            "topic": "wave",
            "sender": "lane:a",
            "body": "hi",
            "action_flag": False,
            "thread_id": 2,
            "reply_to": 24047,
            "recipient": "orchestrator",
        }

    @respx.mock
    def test_omitted_fields_are_absent_from_the_body_not_null(self):
        """v0.1.1 wire-shape parity: a caller that never heard of threads
        sends exactly what it sent before. present-as-null is NOT the same
        thing on a bus model that forbids unknown keys elsewhere."""
        route = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 9})
        )
        routes.post_message("wave", "lane:a", "hi")
        assert _sent(route) == {
            "topic": "wave",
            "sender": "lane:a",
            "body": "hi",
            "action_flag": False,
        }

    @respx.mock
    def test_bus_422_on_a_bad_thread_id_surfaces_as_typed_error(self):
        """The bus validates thread_id against real rows when armed. A reply
        into a thread that does not exist must not read as a success."""
        respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(422, json={"detail": "unknown_thread"})
        )
        result = routes.post_message("wave", "lane:a", "hi", thread_id=999)
        assert result["ok"] is False
        assert result["error"]["status_code"] == 422


# ---------------------------------------------------------------------------
# read_messages -- params the backend ignores TODAY
# ---------------------------------------------------------------------------

class TestReadMessagesPassthroughParams:
    @respx.mock
    def test_new_filters_are_sent_as_query_params(self):
        """The FORWARD-COMPATIBLE half: when the server-side filters land,
        these params are already on the wire."""
        route = respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": []})
        )
        routes.read_messages(topic="wave", limit=10, thread_id=2,
                             recipient="orchestrator", since_id=100)
        params = route.calls.last.request.url.params
        assert params["thread_id"] == "2"
        assert params["recipient"] == "orchestrator"
        assert params["since_id"] == "100"

    @respx.mock
    def test_todays_backend_ignores_them_and_the_tool_does_not_pretend(self):
        """THE LIMITATION, PINNED. Today's GET /messages declares topic+limit
        only and FastAPI drops undeclared query params -- so the backend
        answers with rows that do NOT satisfy thread_id, and this client
        returns them unfiltered rather than inventing a filter it did not
        apply. A caller must filter client-side (get_thread does)."""
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(
                200,
                json={"messages": [
                    {"id": 1, "thread_id": None},
                    {"id": 2, "thread_id": 7},
                ]},
            )
        )
        result = routes.read_messages(thread_id=2)
        assert result["ok"] is True
        assert [m["id"] for m in result["messages"]] == [1, 2]

    @respx.mock
    def test_unused_filters_are_omitted_entirely(self):
        route = respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": []})
        )
        routes.read_messages()
        params = route.calls.last.request.url.params
        for name in ("thread_id", "recipient", "since_id", "topic"):
            assert name not in params


# ---------------------------------------------------------------------------
# list_threads
# ---------------------------------------------------------------------------

class TestListThreads:
    @respx.mock
    def test_success_and_params(self):
        route = respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "threads": [{"id": 2}]})
        )
        result = routes.list_threads(status="open", limit=5)
        assert result["ok"] is True
        assert result["threads"] == [{"id": 2}]
        assert route.calls.last.request.url.params["status"] == "open"
        assert route.calls.last.request.url.params["limit"] == "5"

    @respx.mock
    def test_status_omitted_when_none(self):
        route = respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "threads": []})
        )
        routes.list_threads()
        assert "status" not in route.calls.last.request.url.params

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        result = routes.list_threads()
        assert result["ok"] is False
        assert result["error"]["type"] == "bus_api_error"
        assert result["error"]["status_code"] == 401

    @respx.mock
    def test_unreachable_maps_to_typed_error(self):
        respx.get(f"{BASE}/threads").mock(side_effect=httpx.ConnectError("refused"))
        result = routes.list_threads()
        assert result["error"]["type"] == "bus_unreachable"


# ---------------------------------------------------------------------------
# get_thread -- PRIMARY: direct GET /threads/{id}; FALLBACK: the pre-0.2.1
# client-side composition, now factored into `_get_thread_composed` and
# exercised directly here so the fallback mechanism and the fallback TRIGGER
# (see TestGetThreadFallbackWiring below) are tested independently.
# ---------------------------------------------------------------------------

class TestGetThreadComposedFallback:
    """Tests `_get_thread_composed` directly -- the exact pre-0.2.1 behavior
    of `get_thread`, unchanged, now reachable only when the direct route is
    absent or flagged dark. See TestGetThreadDirectRoute for proof that
    `get_thread` actually falls into this on a 404 / threads_disabled."""

    @respx.mock
    def test_composes_thread_row_plus_its_messages_oldest_first(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(
                200,
                json={"messages": [
                    {"id": 12, "thread_id": 2, "body": "second"},
                    {"id": 10, "thread_id": 2, "body": "root"},
                    {"id": 11, "thread_id": 5, "body": "other thread"},
                    {"id": 9, "thread_id": None, "body": "plain topic message"},
                ]},
            )
        )
        result = routes._get_thread_composed(2, routes._SCAN_LIMIT)
        assert result["ok"] is True
        assert result["thread"] == {"id": 2, "topic": "wave"}
        assert [m["id"] for m in result["messages"]] == [10, 12]
        assert result["message_count"] == 2
        assert result["composed"] is True

    @respx.mock
    def test_reads_messages_filtered_by_the_threads_own_topic(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        msgs = respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": []})
        )
        routes._get_thread_composed(2, routes._SCAN_LIMIT)
        assert msgs.calls.last.request.url.params["topic"] == "wave"

    @respx.mock
    def test_scan_truncated_is_true_when_the_page_is_full(self):
        """THE HONESTY FIELD. A full page means older replies may exist that
        this call could not see -- reported, never assumed away."""
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(
                200, json={"messages": [{"id": i, "thread_id": 2} for i in range(3)]}
            )
        )
        result = routes._get_thread_composed(2, 3)
        assert result["scanned"] == 3
        assert result["scan_truncated"] is True

    @respx.mock
    def test_scan_truncated_false_on_a_short_page(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": 1, "thread_id": 2}]})
        )
        assert routes._get_thread_composed(2, 50)["scan_truncated"] is False

    @respx.mock
    def test_falls_back_to_the_archived_bucket(self):
        """GET /threads with no status EXCLUDES archived. A by-id lookup that
        stopped there would report a real thread as missing."""
        respx.get(f"{BASE}/threads").mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "threads": []}),
                httpx.Response(
                    200, json={"ok": True, "threads": [{"id": 2, "topic": "old"}]}
                ),
            ]
        )
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": []})
        )
        result = routes._get_thread_composed(2, routes._SCAN_LIMIT)
        assert result["ok"] is True
        assert result["thread"]["topic"] == "old"

    @respx.mock
    def test_absent_thread_reports_not_visible_not_not_found(self):
        """'Not in the scan window' and 'does not exist' are different
        answers, and this client can only honestly give the first."""
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "threads": []})
        )
        result = routes._get_thread_composed(999, routes._SCAN_LIMIT)
        assert result["ok"] is False
        assert result["error"]["type"] == "thread_not_visible"
        assert "does not exist" in result["error"]["reason"]

    @respx.mock
    def test_thread_list_error_propagates_untouched(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        result = routes._get_thread_composed(2, routes._SCAN_LIMIT)
        assert result["error"]["status_code"] == 401


class TestGetThreadDirectRoute:
    """`get_thread`'s PRIMARY path as of 0.2.1: one direct
    GET /threads/{id} call against the bus's own by-id route (landed
    2026-09-10, backend/coordination_bus.py `get_thread_route`)."""

    @respx.mock
    def test_direct_route_success_returns_the_bus_shape_composed_false(self):
        respx.get(f"{BASE}/threads/2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "thread": {"id": 2, "topic": "wave"},
                    "messages": [{"id": 10, "thread_id": 2, "body": "root"}],
                    "_meta": {"message_count": 1, "limit": 200, "truncated": False},
                },
            )
        )
        result = routes.get_thread(2)
        assert result["ok"] is True
        assert result["thread"] == {"id": 2, "topic": "wave"}
        assert [m["id"] for m in result["messages"]] == [10]
        assert result["message_count"] == 1
        assert result["truncated"] is False
        assert result["composed"] is False

    @respx.mock
    def test_the_limit_argument_is_sent_as_a_query_param(self):
        route = respx.get(f"{BASE}/threads/2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True, "thread": {"id": 2, "topic": "wave"}, "messages": [],
                    "_meta": {"message_count": 0, "limit": 7, "truncated": False},
                },
            )
        )
        routes.get_thread(2, limit=7)
        assert route.calls.last.request.url.params["limit"] == "7"

    @respx.mock
    def test_other_direct_route_errors_propagate_untouched_never_fall_back(self):
        """A 500 (or a 401, if the route ever gets gated) is a REAL failure,
        not a 'route absent' signal -- falling back here would mask it behind
        a stale composed answer instead of surfacing it."""
        respx.get(f"{BASE}/threads/2").mock(
            return_value=httpx.Response(500, json={"detail": "boom"})
        )
        result = routes.get_thread(2)
        assert result["ok"] is False
        assert result["error"]["status_code"] == 500


class TestGetThreadFallbackWiring:
    """Proves `get_thread` actually falls into `_get_thread_composed` on the
    two 'this backend cannot answer directly' signals: a genuine 404 (route
    does not exist on an older backend) and a 200 ok=False threads_disabled
    body (route exists but BUS_THREADS_ENABLED is off)."""

    @respx.mock
    def test_404_falls_back_to_composition_with_composed_true(self):
        respx.get(f"{BASE}/threads/2").mock(
            return_value=httpx.Response(
                404, json={"ok": False, "error": {"type": "unknown_thread", "thread_id": 2}}
            )
        )
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(
                200, json={"messages": [{"id": 10, "thread_id": 2, "body": "root"}]}
            )
        )
        result = routes.get_thread(2)
        assert result["ok"] is True
        assert result["composed"] is True
        assert result["thread"] == {"id": 2, "topic": "wave"}
        assert [m["id"] for m in result["messages"]] == [10]

    @respx.mock
    def test_threads_disabled_falls_back_to_composition(self):
        respx.get(f"{BASE}/threads/2").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "error": {"type": "threads_disabled"}}
            )
        )
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.get(f"{BASE}/messages").mock(
            return_value=httpx.Response(200, json={"messages": []})
        )
        result = routes.get_thread(2)
        assert result["ok"] is True
        assert result["composed"] is True

    @respx.mock
    def test_a_genuinely_absent_thread_after_fallback_still_reports_not_visible(self):
        respx.get(f"{BASE}/threads/999").mock(
            return_value=httpx.Response(
                404, json={"ok": False, "error": {"type": "unknown_thread", "thread_id": 999}}
            )
        )
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "threads": []})
        )
        result = routes.get_thread(999)
        assert result["ok"] is False
        assert result["error"]["type"] == "thread_not_visible"


class TestGetThreadNeverRaises:
    """`thread_id` is now interpolated straight into the request URL
    (GET /threads/{id}) -- the same path-injection hazard `resolve_thread`
    guards against with `_coerce_id`. Reusing the exact adversarial literal
    from the task-tool traversal fixture (tests/test_board.py's
    '../lanes/converge') here: a thread_id normalizing to
    '/api/bus/threads/../lanes/converge' would collapse to
    '/api/bus/lanes/converge', a real route, if this were not refused
    client-side first."""

    @respx.mock
    @pytest.mark.parametrize("bad", ["../lanes/converge", "abc", None, "", 0, -2, ["1"]])
    def test_bad_thread_id_returns_an_error_dict_never_reaching_the_network(self, bad):
        # No respx route registered for anything -- if the refusal ever
        # leaked to the network, respx would raise instead of this
        # coincidentally returning an ok=False-shaped dict.
        result = routes.get_thread(bad)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_id"
        assert result["error"]["field"] == "thread_id"

    @respx.mock
    def test_the_traversal_guard_can_fail(self):
        """POSITIVE CONTROL: without `_coerce_id`'s int-and->=1 check, a
        non-numeric thread_id would sail straight into the URL. Pin the
        guard's own mechanism, not just its outcome."""
        assert routes._coerce_id("../lanes/converge", "thread_id", "get_thread")[0] is None

    @respx.mock
    def test_a_numeric_string_id_still_reaches_the_network(self):
        route = respx.get(f"{BASE}/threads/2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True, "thread": {"id": 2, "topic": "wave"}, "messages": [],
                    "_meta": {"message_count": 0, "limit": 200, "truncated": False},
                },
            )
        )
        assert routes.get_thread("2")["ok"] is True
        assert route.called


# ---------------------------------------------------------------------------
# open_thread
# ---------------------------------------------------------------------------

class TestOpenThread:
    @respx.mock
    def test_request_shape_matches_ThreadOpenRequest(self):
        route = respx.post(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "thread_id": 3})
        )
        result = routes.open_thread(
            "wave", "A title", "body text", kind="DECIDE",
            opened_by="orchestrator", recipient="operator", action_flag=True,
        )
        assert result["ok"] is True
        assert _sent(route) == {
            "topic": "wave",
            "title": "A title",
            "opened_by": "orchestrator",
            "body": "body text",
            "kind": "DECIDE",
            "recipient": "operator",
            "action_flag": True,
        }

    @respx.mock
    def test_optional_fields_omitted_not_null(self):
        """extra="forbid" makes the wire shape part of the contract; null-vs-
        absent is a distinction worth pinning on this model."""
        route = respx.post(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "thread_id": 3})
        )
        routes.open_thread("wave", "t", "b", opened_by="me")
        assert _sent(route) == {
            "topic": "wave", "title": "t", "opened_by": "me",
            "body": "b", "action_flag": False,
        }

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.post(f"{BASE}/threads").mock(
            return_value=httpx.Response(401, json={"detail": "missing secret"})
        )
        result = routes.open_thread("wave", "t", "b")
        assert result["ok"] is False
        assert result["error"]["status_code"] == 401


# ---------------------------------------------------------------------------
# reply_in_thread
# ---------------------------------------------------------------------------

class TestReplyInThread:
    @respx.mock
    def test_looks_up_the_topic_when_omitted(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        route = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 40})
        )
        result = routes.reply_in_thread(2, "my reply", reply_to=24047, sender="lane:a")
        assert result["ok"] is True
        assert _sent(route) == {
            "topic": "wave",
            "sender": "lane:a",
            "body": "my reply",
            "action_flag": False,
            "thread_id": 2,
            "reply_to": 24047,
        }

    @respx.mock
    def test_explicit_topic_skips_the_lookup(self):
        threads = respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "threads": []})
        )
        route = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 41})
        )
        routes.reply_in_thread(2, "reply", topic="wave", sender="lane:a")
        assert not threads.called
        assert _sent(route)["topic"] == "wave"

    @respx.mock
    def test_reply_to_is_left_unset_when_omitted_never_guessed(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "threads": [
                    {"id": 2, "topic": "wave", "root_message_id": 24047}
                ]},
            )
        )
        route = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 42})
        )
        routes.reply_in_thread(2, "reply", sender="lane:a")
        assert "reply_to" not in _sent(route)

    @respx.mock
    def test_unknown_thread_refuses_before_posting(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(200, json={"ok": True, "threads": []})
        )
        post = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 43})
        )
        result = routes.reply_in_thread(999, "reply")
        assert result["error"]["type"] == "thread_not_visible"
        assert not post.called


# ---------------------------------------------------------------------------
# resolve_thread
# ---------------------------------------------------------------------------

class TestResolveThread:
    @respx.mock
    def test_success_shape(self):
        route = respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(200, json={"ok": True, "status": "resolved"})
        )
        result = routes.resolve_thread(2, resolved_by="lane:a")
        assert result["ok"] is True
        assert result["note_posted"] is False
        assert _sent(route) == {"resolved_by": "lane:a"}

    @respx.mock
    @pytest.mark.parametrize("claim", ["operator", "OPERATOR", "Operator", " operator "])
    def test_operator_resolution_refused_client_side(self, claim):
        """FIRES. No respx route registered for the resolve path: if the
        refusal ever leaked to the network respx would raise, so this cannot
        pass by coincidentally returning an ok=False-shaped dict."""
        result = routes.resolve_thread(2, resolved_by=claim)
        assert result["ok"] is False
        assert result["error"]["type"] == "operator_resolution_refused"
        assert "X-Bus-Operator-Secret" in result["error"]["reason"]

    @respx.mock
    def test_non_operator_resolution_stays_silent(self):
        """STAYS SILENT. The same check must not fire on a normal role."""
        respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = routes.resolve_thread(2, resolved_by="lane:bus-mcp-tools")
        assert result["ok"] is True

    @respx.mock
    def test_note_is_posted_as_a_reply_then_the_thread_resolves(self):
        """ThreadResolveRequest has no `note` field and does not forbid extra
        keys -- a note on that body would be accepted and dropped. So it goes
        where a human will actually read it."""
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        msg = respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(200, json={"id": 50})
        )
        resolve = respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = routes.resolve_thread(2, resolved_by="lane:a", note="done, verified")
        assert result["note_posted"] is True
        assert _sent(msg)["body"] == "done, verified"
        assert _sent(msg)["thread_id"] == 2
        assert _sent(resolve) == {"resolved_by": "lane:a"}

    @respx.mock
    def test_note_body_never_reaches_the_resolve_payload(self):
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.post(f"{BASE}/message").mock(return_value=httpx.Response(200, json={"id": 51}))
        resolve = respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.resolve_thread(2, resolved_by="lane:a", note="a note")
        assert "note" not in _sent(resolve)

    @respx.mock
    def test_failed_note_leaves_the_thread_open(self):
        """Ordering is the guarantee: a note that vanished plus a thread
        closed without its stated reason is the failure this avoids."""
        respx.get(f"{BASE}/threads").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "threads": [{"id": 2, "topic": "wave"}]}
            )
        )
        respx.post(f"{BASE}/message").mock(
            return_value=httpx.Response(422, json={"detail": "thread resolved"})
        )
        resolve = respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = routes.resolve_thread(2, resolved_by="lane:a", note="a note")
        assert result["ok"] is False
        assert not resolve.called

    @respx.mock
    def test_403_from_the_bus_maps_to_typed_error(self):
        respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(403, json={"detail": "policy_refusal"})
        )
        result = routes.resolve_thread(2, resolved_by="lane:a")
        assert result["error"]["status_code"] == 403


class TestResolveThreadNeverRaises:
    """`int(thread_id)` sat inside the try block with ValueError outside the
    except tuple -- so a non-numeric thread_id raised through the module's
    "never a raw exception" contract instead of returning the error dict."""

    @respx.mock
    @pytest.mark.parametrize("bad", ["abc", None, "", 0, -2, ["1"]])
    def test_bad_thread_id_returns_an_error_dict(self, bad):
        result = routes.resolve_thread(bad, resolved_by="lane:a")
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_id"
        assert result["error"]["field"] == "thread_id"

    @respx.mock
    def test_the_operator_refusal_still_wins_over_a_valid_id(self):
        """Order check: a well-formed id must not let an operator claim
        through, and a bad id must not mask one either."""
        assert routes.resolve_thread(2, resolved_by="operator")["error"]["type"] == (
            "operator_resolution_refused"
        )

    @respx.mock
    def test_a_numeric_string_id_still_reaches_the_network(self):
        route = respx.post(f"{BASE}/threads/2/resolve").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        assert routes.resolve_thread("2", resolved_by="lane:a")["ok"] is True
        assert route.called
