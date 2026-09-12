"""Board-column parity: does the LIVE bus board actually match what this
package believes about it?

Nothing before this file compared bus-mcp's board handling against a real
board response. That gap already bit once: alphahive master@265106c1 added
`origin_thread_id` as the board's 15th key, and this package only learned
about it because a human noticed -- not because any test here could have.

This file checks two independent claims against ONE committed fixture
(tests/fixtures/board_row_live.json -- a real row captured verbatim from
GET http://127.0.0.1:8100/api/bus/tasks/board, see that file's `_note`):

1. No column in `config.BOARD_SENSITIVE_COLUMNS` (the allowlist that used to
   be hand-duplicated across server.py and routes.py -- see config.py) is
   present on a real board row. If one ever is, that is a backend regression
   serious enough to report loudly, not silently swallow.
2. The real row's key set matches `EXPECTED_BOARD_KEYS`, a literal recorded
   here independently of the fixture. A backend that adds/removes/renames a
   column changes the fixture's key set but NOT this literal, so the two
   diverge and the test fails -- instead of the silent drift that let
   `origin_thread_id` go unnoticed.

CAVEAT (the parity this buys, and the parity it does not): this is a
fixture check, not a live one. It proves the fixture is honest about the
moment it was captured; it does NOT re-poll the live board on every test
run. A future new column still requires a human (or a scheduled live-smoke
job, which does not exist yet) to notice, recapture the fixture, and update
EXPECTED_BOARD_KEYS -- exactly the manual step this file cannot remove, only
make loud and mechanical instead of silent.
"""
from __future__ import annotations

import json
from pathlib import Path

from bus_mcp import config

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "board_row_live.json"

# The board row shape this package believes it is dealing with, recorded
# independently of the fixture file so the two can actually diverge (a
# constant derived FROM the fixture would make the parity test tautological
# -- always true, unable to ever fail).
EXPECTED_BOARD_KEYS = frozenset(
    {
        "task_id",
        "title",
        "task_class",
        "status",
        "posted_at",
        "attempts",
        "max_attempts",
        "claimed_by",
        "claimed_at",
        "started_at",
        "finished_at",
        "exit_code",
        "verify_passed",
        "archived_at",
        "origin_thread_id",
    }
)


def _load_fixture_row() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        payload = json.load(f)
    return payload["row"]


def _find_sensitive_leaks(row: dict) -> list[str]:
    """Sensitive-list columns present as keys on `row`, sorted. Empty means
    clean."""
    return sorted(k for k in row if k in config.BOARD_SENSITIVE_COLUMNS)


def _key_set_diff(row: dict, expected: frozenset) -> tuple[set, set]:
    """(extra, missing) between `row`'s keys and `expected`. Both empty
    means the shapes match exactly."""
    keys = set(row.keys())
    return keys - expected, expected - keys


# ---------------------------------------------------------------------------
# The two parity claims against the real, committed fixture.
# ---------------------------------------------------------------------------

class TestLiveBoardRowParity:
    def test_no_sensitive_column_leaks_on_the_real_row(self):
        row = _load_fixture_row()
        leaks = _find_sensitive_leaks(row)
        assert leaks == [], (
            f"sensitive column(s) {leaks} appeared on a REAL board row -- "
            "this is a backend allowlist regression, not a test bug"
        )

    def test_real_row_key_set_matches_what_the_client_expects(self):
        row = _load_fixture_row()
        extra, missing = _key_set_diff(row, EXPECTED_BOARD_KEYS)
        assert not extra, (
            f"live board row has key(s) {sorted(extra)} that "
            "EXPECTED_BOARD_KEYS in this file does not know about -- the "
            "backend added/renamed a column; recapture the fixture and "
            "update EXPECTED_BOARD_KEYS deliberately"
        )
        assert not missing, (
            f"live board row is MISSING key(s) {sorted(missing)} that "
            "EXPECTED_BOARD_KEYS expects -- the backend dropped/renamed a "
            "column; recapture the fixture and update EXPECTED_BOARD_KEYS "
            "deliberately"
        )


# ---------------------------------------------------------------------------
# POSITIVE CONTROL. A check never shown to fire is not a check -- these
# mutate an IN-MEMORY copy of the fixture row (never the committed file) and
# assert the detector helpers above catch it. See this lane's final report
# for the companion empirical run: the committed fixture was actually
# mutated on disk and pytest actually went red, then the fixture was
# reverted -- these tests are the permanent, always-green proof that the
# SAME mutation would be caught again.
# ---------------------------------------------------------------------------

class TestParityChecksHavePositiveControl:
    def test_sensitive_leak_detector_FIRES_on_an_injected_sensitive_column(self):
        row = dict(_load_fixture_row())
        row["claim_token"] = "should-never-appear-here"
        assert _find_sensitive_leaks(row) == ["claim_token"]

    def test_sensitive_leak_detector_STAYS_SILENT_on_the_real_row(self):
        row = _load_fixture_row()
        assert _find_sensitive_leaks(row) == []

    def test_key_set_check_FIRES_on_an_unexpected_extra_column(self):
        row = dict(_load_fixture_row())
        row["mystery_new_column"] = "x"
        extra, missing = _key_set_diff(row, EXPECTED_BOARD_KEYS)
        assert extra == {"mystery_new_column"}
        assert missing == set()

    def test_key_set_check_FIRES_on_a_missing_expected_column(self):
        row = dict(_load_fixture_row())
        del row["origin_thread_id"]
        extra, missing = _key_set_diff(row, EXPECTED_BOARD_KEYS)
        assert extra == set()
        assert missing == {"origin_thread_id"}

    def test_key_set_check_STAYS_SILENT_on_the_real_row(self):
        row = _load_fixture_row()
        extra, missing = _key_set_diff(row, EXPECTED_BOARD_KEYS)
        assert not extra and not missing


# ---------------------------------------------------------------------------
# The deduplication itself: server.py and routes.py must render the SAME
# text from the SAME constant, not two hand-typed copies that can drift.
# ---------------------------------------------------------------------------

class TestSensitiveColumnListIsSharedNotDuplicated:
    def test_routes_docstring_uses_the_shared_constant(self):
        from bus_mcp import routes

        assert config.BOARD_SENSITIVE_COLUMNS_TEXT in routes.list_tasks_board.__doc__
        for col in config.BOARD_SENSITIVE_COLUMNS:
            assert col in routes.list_tasks_board.__doc__

    def test_server_tool_description_uses_the_shared_constant(self):
        import asyncio

        from bus_mcp import server

        async def _get_description() -> str:
            tool = await server.mcp.get_tool("list_tasks_board")
            return tool.description

        description = asyncio.run(_get_description())
        assert config.BOARD_SENSITIVE_COLUMNS_TEXT in description
