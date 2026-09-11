"""respx-mocked tests for the validation + dispatch tools.

What these pin beyond "the call went out": the DERIVATION in
request_validation (subject_kind from the subject_ref prefix, refused rather
than guessed when absent), the FIELD-NAME translation in vote (`evidence` ->
the bus's `evidence_ref`), and the path-shape guards on dispatch_id -- which
is interpolated into a URL.
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


class TestListValidations:
    @respx.mock
    def test_success_and_filters(self):
        route = respx.get(f"{BASE}/validations").mock(
            return_value=httpx.Response(200, json={"ok": True, "validations": [{"id": 5}]})
        )
        result = routes.list_validations(limit=10, verdict="pending")
        assert result["ok"] is True
        assert result["validations"] == [{"id": 5}]
        params = route.calls.last.request.url.params
        assert params["verdict"] == "pending"
        assert params["limit"] == "10"

    @respx.mock
    def test_unset_filters_omitted(self):
        route = respx.get(f"{BASE}/validations").mock(
            return_value=httpx.Response(200, json={"ok": True, "validations": []})
        )
        routes.list_validations()
        params = route.calls.last.request.url.params
        for name in ("verdict", "subject_ref", "thread_id"):
            assert name not in params

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/validations").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        result = routes.list_validations()
        assert result["ok"] is False
        assert result["error"]["status_code"] == 401


class TestGetValidation:
    @respx.mock
    def test_success(self):
        respx.get(f"{BASE}/validations/5").mock(
            return_value=httpx.Response(200, json={"ok": True, "validation": {"id": 5}})
        )
        assert routes.get_validation(5)["validation"] == {"id": 5}

    @respx.mock
    def test_404_is_a_typed_error_not_an_empty_result(self):
        """'Absent' and 'you could not see it' must stay different answers."""
        respx.get(f"{BASE}/validations/999").mock(
            return_value=httpx.Response(404, json={"detail": "unknown_validation"})
        )
        result = routes.get_validation(999)
        assert result["ok"] is False
        assert result["error"]["status_code"] == 404

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/validations/5").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.get_validation(5)["error"]["status_code"] == 401


class TestRequestValidation:
    @respx.mock
    @pytest.mark.parametrize(
        "subject_ref,expected_kind",
        [
            ("message:24047", "message"),
            ("task:nightly-digest", "task"),
            ("proposal:bus-mcp:README.md", "proposal"),
        ],
    )
    def test_subject_kind_is_derived_from_the_prefix(self, subject_ref, expected_kind):
        route = respx.post(f"{BASE}/validations").mock(
            return_value=httpx.Response(200, json={"ok": True, "validation_id": 1})
        )
        result = routes.request_validation(subject_ref, requested_by="lane:a")
        assert result["ok"] is True
        assert _sent(route)["subject_kind"] == expected_kind

    @respx.mock
    def test_request_shape_matches_ValidationRequest(self):
        route = respx.post(f"{BASE}/validations").mock(
            return_value=httpx.Response(200, json={"ok": True, "validation_id": 1})
        )
        routes.request_validation(
            "message:24047", evidence_refs="report.md", requested_by="lane:a"
        )
        assert _sent(route) == {
            "subject_kind": "message",
            "subject_ref": "message:24047",
            "requested_by": "lane:a",
            "evidence_refs": "report.md",
        }

    @respx.mock
    def test_tier_is_never_sent(self):
        """ValidationRequest forbids extra keys and derives tier itself. A
        client that sent one would 422 -- but more to the point, a client that
        CHOSE one would be choosing who has to sign off."""
        route = respx.post(f"{BASE}/validations").mock(
            return_value=httpx.Response(200, json={"ok": True, "validation_id": 1})
        )
        routes.request_validation("message:1")
        assert "tier" not in _sent(route)

    @respx.mock
    def test_unprefixed_subject_ref_is_refused_before_the_call(self):
        """FIRES. No respx route for the POST: a leak to the network raises."""
        result = routes.request_validation("just-some-string")
        assert result["ok"] is False
        assert result["error"]["type"] == "unknown_subject_kind"
        for prefix in ("message:", "task:", "proposal:"):
            assert prefix in result["error"]["reason"]

    @respx.mock
    def test_explicit_subject_kind_overrides_derivation(self):
        """STAYS SILENT: a caller who names the kind is not second-guessed."""
        route = respx.post(f"{BASE}/validations").mock(
            return_value=httpx.Response(200, json={"ok": True, "validation_id": 1})
        )
        routes.request_validation("weird-ref", subject_kind="proposal")
        assert _sent(route)["subject_kind"] == "proposal"

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.post(f"{BASE}/validations").mock(
            return_value=httpx.Response(401, json={"detail": "missing secret"})
        )
        assert routes.request_validation("message:1")["error"]["status_code"] == 401


class TestVote:
    @respx.mock
    def test_request_shape_matches_ValidationVoteRequest(self):
        route = respx.post(f"{BASE}/validations/5/vote").mock(
            return_value=httpx.Response(200, json={"ok": True, "verdict": "confirmed"})
        )
        result = routes.vote(5, "a1b2c3d4e5f6", "confirmed", "tests/out.xml", voter="lane:a")
        assert result["ok"] is True
        assert _sent(route) == {
            "voter": "lane:a",
            "dispatch_id": "a1b2c3d4e5f6",
            "verdict": "confirmed",
            "evidence_ref": "tests/out.xml",
        }

    @respx.mock
    @pytest.mark.parametrize(
        "bad", ["", "nothex", "A1B2C3D4E5F6", "a1b2c3d4e5f", "a1b2c3d4e5f66", None]
    )
    def test_malformed_dispatch_id_refused_before_the_call(self, bad):
        result = routes.vote(5, bad, "confirmed", "ref")
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_dispatch_id"

    @respx.mock
    def test_self_confirmation_403_surfaces_typed(self):
        respx.post(f"{BASE}/validations/5/vote").mock(
            return_value=httpx.Response(403, json={"detail": "self_confirmation"})
        )
        result = routes.vote(5, "a1b2c3d4e5f6", "confirmed", "ref")
        assert result["error"]["status_code"] == 403

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.post(f"{BASE}/validations/5/vote").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.vote(5, "a1b2c3d4e5f6", "refuted", "ref")["error"]["status_code"] == 401


class TestDispatches:
    @respx.mock
    def test_mint_request_shape(self):
        route = respx.post(f"{BASE}/dispatches").mock(
            return_value=httpx.Response(200, json={"ok": True, "dispatch_id": "a1b2c3d4e5f6"})
        )
        result = routes.mint_dispatch("bus-mcp-tools", "bus-mcp", "wrap the bus routes",
                                      minted_by="orchestrator")
        assert result["dispatch_id"] == "a1b2c3d4e5f6"
        assert _sent(route) == {
            "minted_by": "orchestrator",
            "lane": "bus-mcp-tools",
            "repo": "bus-mcp",
            "purpose": "wrap the bus routes",
        }

    @respx.mock
    def test_mint_duplicate_409_surfaces_typed(self):
        respx.post(f"{BASE}/dispatches").mock(
            return_value=httpx.Response(409, json={"detail": "duplicate_dispatch"})
        )
        assert routes.mint_dispatch("l", "r", "p")["error"]["status_code"] == 409

    @respx.mock
    def test_report_request_shape(self):
        route = respx.post(f"{BASE}/dispatches/a1b2c3d4e5f6/report").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = routes.report_dispatch("a1b2c3d4e5f6", "staged/report.md")
        assert result["ok"] is True
        assert _sent(route) == {"report_ref": "staged/report.md"}

    @respx.mock
    @pytest.mark.parametrize("bad", ["../../status", "a1b2c3d4e5f6/../x", "ZZZZZZZZZZZZ"])
    def test_report_path_injection_refused_before_the_call(self, bad):
        """FIRES, fail-closed: dispatch_id lands in the URL path."""
        result = routes.report_dispatch(bad, "ref")
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_dispatch_id"

    @respx.mock
    def test_report_unknown_dispatch_404_surfaces_typed(self):
        respx.post(f"{BASE}/dispatches/a1b2c3d4e5f6/report").mock(
            return_value=httpx.Response(404, json={"detail": "unknown_dispatch"})
        )
        assert routes.report_dispatch("a1b2c3d4e5f6", "ref")["error"]["status_code"] == 404

    @respx.mock
    def test_list_dispatches_success(self):
        route = respx.get(f"{BASE}/dispatches").mock(
            return_value=httpx.Response(200, json={"ok": True, "dispatches": [{"id": 1}]})
        )
        result = routes.list_dispatches(status="open")
        assert result["dispatches"] == [{"id": 1}]
        assert route.calls.last.request.url.params["status"] == "open"

    @respx.mock
    def test_list_dispatches_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/dispatches").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.list_dispatches()["error"]["status_code"] == 401


# ---------------------------------------------------------------------------
# The module's "never a raw exception" contract, on the paths that broke it
#
# A bare int() / .startswith() inside the try block does not honour the
# header's promise: ValueError and AttributeError are not in the except
# tuple. Unreachable through MCP (FastMCP rejects a bad type at the tool
# boundary), but this module is importable as a library, and a contract that
# holds only because something upstream enforces it is not the written
# contract. Each test below CRASHED before the fix.
# ---------------------------------------------------------------------------

class TestNeverRaises:
    @respx.mock
    @pytest.mark.parametrize("bad", ["abc", None, "1; DROP", "", 0, -3, [7]])
    def test_get_validation_returns_an_error_dict_never_raises(self, bad):
        result = routes.get_validation(bad)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_id"
        assert result["error"]["field"] == "validation_id"

    @respx.mock
    @pytest.mark.parametrize("bad", ["abc", None, "", 0, -1])
    def test_vote_returns_an_error_dict_never_raises(self, bad):
        result = routes.vote(bad, "a1b2c3d4e5f6", "confirmed", "ref")
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_id"

    @respx.mock
    @pytest.mark.parametrize("bad", [None, 7, ["message:1"], {"a": 1}])
    def test_request_validation_non_string_subject_ref_never_raises(self, bad):
        result = routes.request_validation(bad)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_subject_ref"

    @respx.mock
    def test_a_valid_id_still_reaches_the_network(self):
        """STAYS SILENT: the coercion must not refuse a legitimate id."""
        route = respx.get(f"{BASE}/validations/5").mock(
            return_value=httpx.Response(200, json={"ok": True, "validation": {"id": 5}})
        )
        assert routes.get_validation("5")["ok"] is True
        assert route.called

    @respx.mock
    def test_every_tool_result_including_these_refusals_carries_agent_id(self):
        assert "agent_id" in routes.get_validation("abc")
        assert "agent_id" in routes.request_validation(None)
