"""respx-mocked tests for the board / task-claim / worker / events tools.

The centre of gravity here is the TASK-CLAIM GATE: the three mutating task
tools ship dark, and a gate that has never been shown to fire on a known-bad
input and stay silent on a known-good one is not a gate. Both halves are
below, per tool, plus the stacking order against the write gate.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from bus_mcp import config, routes

BASE = "http://127.0.0.1:8100/api/bus"

# (fn, args) for each task-claim-gated route -- the dark three.
TASK_CLAIM_FN_ARGS = [
    (routes.claim_task, ("t1",)),
    (routes.heartbeat_task, ("t1", "tok")),
    (routes.finish_task, ("t1", "tok", "done")),
]


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
def task_claim_enabled(monkeypatch):
    monkeypatch.setenv("BUS_MCP_ENABLE_TASK_CLAIM", "1")


# ---------------------------------------------------------------------------
# The task-claim gate
# ---------------------------------------------------------------------------

class TestTaskClaimGate:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("BUS_MCP_ENABLE_TASK_CLAIM", raising=False)
        assert config.group_enabled(config.GROUP_TASK_CLAIM) is False

    @respx.mock
    @pytest.mark.parametrize("fn,args", TASK_CLAIM_FN_ARGS)
    def test_FIRES_with_the_env_unset(self, fn, args, monkeypatch):
        """FIRES. conftest arms BUS_MCP_ENABLE_WRITE, so the only thing that
        can refuse here is the task-claim gate. No respx route is registered:
        a leak to the network would raise rather than pass."""
        monkeypatch.delenv("BUS_MCP_ENABLE_TASK_CLAIM", raising=False)
        result = fn(*args)
        assert result["ok"] is False
        assert result["error"]["type"] == "policy_refusal"
        assert result["error"]["group"] == "task_claim"
        assert result["error"]["required_env"] == "BUS_MCP_ENABLE_TASK_CLAIM"
        assert result["error"]["tool"] == fn.__name__

    @respx.mock
    @pytest.mark.parametrize("fn,args", TASK_CLAIM_FN_ARGS)
    def test_refusal_names_the_gate_and_the_reason(self, fn, args, monkeypatch):
        """A refusal that does not say WHY sends the reader to the source."""
        monkeypatch.delenv("BUS_MCP_ENABLE_TASK_CLAIM", raising=False)
        error = fn(*args)["error"]
        assert "BUS_MCP_ENABLE_TASK_CLAIM" in error["message"]
        assert "claimant class not yet signed" in error["reason"]
        assert "claimant class not yet signed" in error["message"]

    @respx.mock
    def test_STAYS_SILENT_with_the_env_set(self, task_claim_enabled):
        """STAYS SILENT. Same call, gate armed, reaches the (mocked) bus."""
        route = respx.post(f"{BASE}/tasks/t1/claim").mock(
            return_value=httpx.Response(200, json={"ok": True, "claim_token": "x"})
        )
        result = routes.claim_task("t1")
        assert result["ok"] is True
        assert route.called

    @respx.mock
    def test_STAYS_SILENT_for_heartbeat_and_finish(self, task_claim_enabled):
        hb = respx.post(f"{BASE}/tasks/t1/heartbeat").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        fin = respx.post(f"{BASE}/tasks/t1/finish").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        assert routes.heartbeat_task("t1", "tok")["ok"] is True
        assert routes.finish_task("t1", "tok", "done")["ok"] is True
        assert hb.called and fin.called

    @respx.mock
    @pytest.mark.parametrize("fn,args", TASK_CLAIM_FN_ARGS)
    def test_write_gate_answers_first_when_both_are_off(self, fn, args, monkeypatch):
        """STACKING ORDER. A server with writes off must say 'writes are off'
        -- the same answer it gives for every other write -- not leak that a
        narrower rails gate also exists."""
        monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)
        monkeypatch.delenv("BUS_MCP_ENABLE_TASK_CLAIM", raising=False)
        assert fn(*args)["error"]["group"] == "write"

    @respx.mock
    @pytest.mark.parametrize("fn,args", TASK_CLAIM_FN_ARGS)
    def test_task_claim_armed_does_not_bypass_the_write_gate(
        self, fn, args, monkeypatch
    ):
        """The narrow gate must never be a way AROUND the broad one."""
        monkeypatch.delenv("BUS_MCP_ENABLE_WRITE", raising=False)
        monkeypatch.setenv("BUS_MCP_ENABLE_TASK_CLAIM", "1")
        assert fn(*args)["error"]["group"] == "write"

    def test_read_tools_are_not_task_claim_gated(self, monkeypatch):
        """SCOPE. The gate covers the three mutations and nothing else --
        reading the board must keep working with it unset."""
        monkeypatch.delenv("BUS_MCP_ENABLE_TASK_CLAIM", raising=False)
        with respx.mock:
            respx.get(f"{BASE}/tasks/board").mock(
                return_value=httpx.Response(200, json={"ok": True, "tasks": []})
            )
            assert routes.list_tasks_board()["ok"] is True


# ---------------------------------------------------------------------------
# list_tasks_board
# ---------------------------------------------------------------------------

class TestListTasksBoard:
    @respx.mock
    def test_success_passthrough(self):
        route = respx.get(f"{BASE}/tasks/board").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "tasks": [{"task_id": "a", "status": "pending"}]}
            )
        )
        result = routes.list_tasks_board()
        assert result["ok"] is True
        assert result["tasks"] == [{"task_id": "a", "status": "pending"}]
        assert "include_archived" not in route.calls.last.request.url.params

    @respx.mock
    def test_include_archived_is_the_only_server_side_param(self):
        route = respx.get(f"{BASE}/tasks/board").mock(
            return_value=httpx.Response(200, json={"ok": True, "tasks": []})
        )
        routes.list_tasks_board(status="pending", limit=1, include_archived=True)
        params = route.calls.last.request.url.params
        assert params["include_archived"] == "true"
        assert "status" not in params
        assert "limit" not in params

    @respx.mock
    def test_status_and_limit_filter_client_side_and_say_so(self):
        respx.get(f"{BASE}/tasks/board").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "tasks": [
                    {"task_id": "a", "status": "pending"},
                    {"task_id": "b", "status": "done"},
                    {"task_id": "c", "status": "pending"},
                ]},
            )
        )
        result = routes.list_tasks_board(status="pending", limit=1)
        assert [t["task_id"] for t in result["tasks"]] == ["a"]
        assert result["matched"] == 2
        assert result["returned"] == 1
        assert result["filtered_client_side"] is True

    @respx.mock
    def test_unfiltered_call_is_not_labelled_filtered(self):
        respx.get(f"{BASE}/tasks/board").mock(
            return_value=httpx.Response(200, json={"ok": True, "tasks": [{"status": "x"}]})
        )
        assert routes.list_tasks_board()["filtered_client_side"] is False

    @respx.mock
    def test_taskboard_disabled_value_passes_through_untouched(self):
        """The bus answers a disabled board with a VALUE, not an error. An
        empty task list synthesized here would read as 'no work', which is a
        different and wrong claim."""
        respx.get(f"{BASE}/tasks/board").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "error": {"type": "taskboard_disabled"}}
            )
        )
        result = routes.list_tasks_board(status="pending")
        assert result["error"]["type"] == "taskboard_disabled"
        assert "tasks" not in result

    @respx.mock
    def test_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/tasks/board").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.list_tasks_board()["error"]["status_code"] == 401


# ---------------------------------------------------------------------------
# claim / heartbeat / finish request shapes (gate armed)
# ---------------------------------------------------------------------------

class TestTaskMutationShapes:
    @respx.mock
    def test_claim_request_shape_matches_TaskClaimRequest(self, task_claim_enabled):
        route = respx.post(f"{BASE}/tasks/t1/claim").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.claim_task("t1", owner="lane:a", claim_token="tok-1")
        assert _sent(route) == {"owner": "lane:a", "claim_token": "tok-1"}

    @respx.mock
    def test_claim_mints_a_token_and_returns_it(self, task_claim_enabled):
        """The caller mints the token and the board projection will never
        give it back -- so it must come out of THIS call or it is lost."""
        route = respx.post(f"{BASE}/tasks/t1/claim").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = routes.claim_task("t1", owner="lane:a")
        assert result["claim_token"] == _sent(route)["claim_token"]
        assert len(result["claim_token"]) >= 16

    @respx.mock
    def test_two_claims_mint_different_tokens(self, task_claim_enabled):
        respx.post(f"{BASE}/tasks/t1/claim").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        first = routes.claim_task("t1")["claim_token"]
        second = routes.claim_task("t1")["claim_token"]
        assert first != second

    @respx.mock
    def test_lease_s_is_refused_not_silently_dropped(self, task_claim_enabled):
        """FIRES. The route's model has no lease field; a value passed here
        would vanish and the caller would heartbeat on a schedule nobody
        agreed to. No respx route registered -- a leak would raise."""
        result = routes.claim_task("t1", lease_s=900)
        assert result["ok"] is False
        assert result["error"]["type"] == "unsupported_parameter"
        assert result["error"]["parameter"] == "lease_s"

    @respx.mock
    def test_already_claimed_is_a_value_not_an_exception(self, task_claim_enabled):
        respx.post(f"{BASE}/tasks/t1/claim").mock(
            return_value=httpx.Response(
                200, json={"ok": False, "error": {"type": "already_claimed_or_exhausted"}}
            )
        )
        result = routes.claim_task("t1")
        assert result["error"]["type"] == "already_claimed_or_exhausted"

    @respx.mock
    def test_heartbeat_omits_status_when_not_running(self, task_claim_enabled):
        route = respx.post(f"{BASE}/tasks/t1/heartbeat").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.heartbeat_task("t1", "tok", owner="lane:a")
        assert _sent(route) == {"owner": "lane:a", "claim_token": "tok"}

    @respx.mock
    def test_heartbeat_want_running_maps_to_status_running(self, task_claim_enabled):
        route = respx.post(f"{BASE}/tasks/t1/heartbeat").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.heartbeat_task("t1", "tok", want_running=True, owner="lane:a")
        assert _sent(route)["status"] == "running"

    @respx.mock
    def test_finish_request_shape_matches_TaskFinishRequest(self, task_claim_enabled):
        route = respx.post(f"{BASE}/tasks/t1/finish").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.finish_task(
            "t1", "tok", "done", exit_code=0, verify_passed=True,
            note="suite green", owner="lane:a", result_ref="junit.xml",
        )
        assert _sent(route) == {
            "owner": "lane:a",
            "claim_token": "tok",
            "status": "done",
            "exit_code": 0,
            "verify_passed": True,
            "result_ref": "junit.xml",
            "note": "suite green",
        }

    @respx.mock
    def test_finish_omits_unmeasured_verify_passed(self, task_claim_enabled):
        """Unset is not False and neither is True. A run that verified
        nothing must not ship a verdict about verification."""
        route = respx.post(f"{BASE}/tasks/t1/finish").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.finish_task("t1", "tok", "failed", exit_code=1, owner="lane:a")
        assert "verify_passed" not in _sent(route)

    @respx.mock
    def test_finish_sends_exit_code_zero_rather_than_dropping_it(self, task_claim_enabled):
        """0 is falsy and is a REAL exit code -- the compaction must drop
        None, never a legitimate zero."""
        route = respx.post(f"{BASE}/tasks/t1/finish").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.finish_task("t1", "tok", "done", exit_code=0)
        assert _sent(route)["exit_code"] == 0

    @respx.mock
    def test_finish_sends_verify_passed_false_rather_than_dropping_it(
        self, task_claim_enabled
    ):
        route = respx.post(f"{BASE}/tasks/t1/finish").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        routes.finish_task("t1", "tok", "failed", verify_passed=False)
        assert _sent(route)["verify_passed"] is False

    @respx.mock
    @pytest.mark.parametrize("bad", ["../../status", "t1/../x", "t 1", ""])
    def test_path_injection_in_task_id_refused_before_the_call(self, bad, task_claim_enabled):
        result = routes.claim_task(bad)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_task_id"

    @respx.mock
    def test_401_maps_to_typed_error(self, task_claim_enabled):
        respx.post(f"{BASE}/tasks/t1/claim").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.claim_task("t1")["error"]["status_code"] == 401


# ---------------------------------------------------------------------------
# worker + events
# ---------------------------------------------------------------------------

class TestWorkerAndEvents:
    @respx.mock
    def test_worker_state_success(self):
        respx.get(f"{BASE}/worker").mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "age_s": 12, "as_of": "2026-09-11T01:00:00Z"}
            )
        )
        result = routes.get_worker_state()
        assert result["ok"] is True
        assert result["age_s"] == 12

    @respx.mock
    def test_worker_unavailable_is_an_answer_not_an_error(self):
        """MISSING IS NEVER ZERO. `unavailable` passes through as data."""
        respx.get(f"{BASE}/worker").mock(
            return_value=httpx.Response(
                200, json={"status": "unavailable", "age_s": None, "fires": None}
            )
        )
        result = routes.get_worker_state()
        assert result["ok"] is True
        assert result["status"] == "unavailable"
        assert result["fires"] is None

    @respx.mock
    def test_worker_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/worker").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.get_worker_state()["error"]["status_code"] == 401

    @respx.mock
    def test_events_params_and_cursor(self):
        route = respx.get(f"{BASE}/events").mock(
            return_value=httpx.Response(
                200, json={"events": [{"id": 7}], "cursor": 7}
            )
        )
        result = routes.read_events(since=5, limit=10)
        assert result["cursor"] == 7
        params = route.calls.last.request.url.params
        assert params["since"] == "5"
        assert params["limit"] == "10"

    @respx.mock
    def test_events_empty_is_caught_up_not_an_error(self):
        respx.get(f"{BASE}/events").mock(
            return_value=httpx.Response(200, json={"events": [], "cursor": 5})
        )
        result = routes.read_events(since=5)
        assert result["ok"] is True
        assert result["events"] == []
        assert result["cursor"] == 5

    @respx.mock
    def test_events_401_maps_to_typed_error(self):
        respx.get(f"{BASE}/events").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        assert routes.read_events()["error"]["status_code"] == 401
