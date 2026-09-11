"""Unit tests for scripts/check_readme_counts.py -- the CI count-verification
gate. Exercises the three pure functions (parse claimed counts from the
README's URL-encoded shields.io badge text, parse actual counts from a
junitxml fixture, compare the two) without invoking a real pytest
subprocess, so these tests are fast and deterministic.

Covers the three required scenarios: claimed matches actual, claimed drifts
from actual, and the claim is missing/unparseable from the README.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_readme_counts.py"
_spec = importlib.util.spec_from_file_location("check_readme_counts", _SCRIPT_PATH)
check_readme_counts = importlib.util.module_from_spec(_spec)
sys.modules["check_readme_counts"] = check_readme_counts
_spec.loader.exec_module(check_readme_counts)


# ---------------------------------------------------------------------------
# parse_claimed_counts -- anchored to the real README badge phrasing:
#   [![Tests](https://img.shields.io/badge/tests-63%20%2862%20passing%2C%201%20skipped%29-brightgreen)](#testing)
# i.e. a shields.io badge URL where the label text is URL-encoded:
#   "tests-63 (62 passing, 1 skipped)-brightgreen" percent-encoded.
# ---------------------------------------------------------------------------

def test_parse_claimed_counts_matches_real_badge_phrasing():
    readme = textwrap.dedent(
        """
        # bus-mcp

        [![Tests](https://img.shields.io/badge/tests-63%20%2862%20passing%2C%201%20skipped%29-brightgreen)](#testing)
        """
    )
    claim = check_readme_counts.parse_claimed_counts(readme)
    assert claim == check_readme_counts.Counts(total=63, passed=62, skipped=1)


def test_parse_claimed_counts_missing_claim_returns_none():
    readme = "# bus-mcp\n\nNo test badge in here at all.\n"
    claim = check_readme_counts.parse_claimed_counts(readme)
    assert claim is None


def test_parse_claimed_counts_ignores_unrelated_badges():
    readme = (
        "[![PyPI](https://img.shields.io/pypi/v/bus-mcp)](https://pypi.org/project/bus-mcp/)\n"
        "[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)\n"
    )
    claim = check_readme_counts.parse_claimed_counts(readme)
    assert claim is None


# ---------------------------------------------------------------------------
# parse_actual_counts -- from a pytest --junitxml report
# ---------------------------------------------------------------------------

_JUNIT_MATCH = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="0" skipped="1" tests="89" time="7.9">
</testsuite>
</testsuites>
"""

_JUNIT_WITH_FAILURES = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="3" skipped="1" tests="89" time="7.9">
</testsuite>
</testsuites>
"""


def test_parse_actual_counts_from_junit(tmp_path):
    junit_file = tmp_path / "junit.xml"
    junit_file.write_text(_JUNIT_MATCH, encoding="utf-8")
    actual = check_readme_counts.parse_actual_counts(junit_file)
    assert actual == check_readme_counts.Counts(total=89, passed=88, skipped=1)


def test_parse_actual_counts_subtracts_failures(tmp_path):
    junit_file = tmp_path / "junit.xml"
    junit_file.write_text(_JUNIT_WITH_FAILURES, encoding="utf-8")
    actual = check_readme_counts.parse_actual_counts(junit_file)
    # 89 total - 1 skipped - 3 failures = 85 passed
    assert actual == check_readme_counts.Counts(total=89, passed=85, skipped=1)


# ---------------------------------------------------------------------------
# compare -- the gate's pass/fail decision
# ---------------------------------------------------------------------------

def test_compare_match_passes():
    claimed = check_readme_counts.Counts(total=89, passed=88, skipped=1)
    actual = check_readme_counts.Counts(total=89, passed=88, skipped=1)
    ok, message = check_readme_counts.compare(claimed, actual)
    assert ok is True
    assert "match" in message.lower()


def test_compare_drift_fails():
    claimed = check_readme_counts.Counts(total=63, passed=62, skipped=1)
    actual = check_readme_counts.Counts(total=89, passed=88, skipped=1)
    ok, message = check_readme_counts.compare(claimed, actual)
    assert ok is False
    assert "63" in message and "89" in message


def test_compare_missing_claim_fails():
    ok, message = check_readme_counts.compare(None, check_readme_counts.Counts(total=89, passed=88, skipped=1))
    assert ok is False
    assert "could not find" in message.lower() or "no claim" in message.lower()


# ---------------------------------------------------------------------------
# main() end-to-end against real fixture files on disk
# ---------------------------------------------------------------------------

def test_main_exits_zero_on_match(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "[![Tests](https://img.shields.io/badge/tests-89%20%2888%20passing%2C%201%20skipped%29-brightgreen)](#testing)\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT_MATCH, encoding="utf-8")

    rc = check_readme_counts.main(["--readme", str(readme), "--junit-xml", str(junit)])
    assert rc == 0


def test_main_exits_nonzero_on_drift(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "[![Tests](https://img.shields.io/badge/tests-63%20%2862%20passing%2C%201%20skipped%29-brightgreen)](#testing)\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT_MATCH, encoding="utf-8")

    rc = check_readme_counts.main(["--readme", str(readme), "--junit-xml", str(junit)])
    assert rc != 0


def test_main_exits_nonzero_on_missing_claim(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("Nothing about test counts here.\n", encoding="utf-8")
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT_MATCH, encoding="utf-8")

    rc = check_readme_counts.main(["--readme", str(readme), "--junit-xml", str(junit)])
    assert rc != 0


# ---------------------------------------------------------------------------
# parse_claimed_tool_count + THE LIVE TOOL-COUNT GATE
#
# The test-count gate above runs in CI against a junit report. The tool count
# has no junit report -- its artifact is the REGISTERED TOOL LIST, which means
# importing the server. That import belongs in the suite, not in the
# stdlib-only gate script, so the comparison lives here and CI gates on it by
# running the suite.
# ---------------------------------------------------------------------------

_README_PATH = Path(__file__).resolve().parent.parent / "README.md"


def test_parse_claimed_tool_count_matches_real_badge_phrasing():
    readme = "[![Tools](https://img.shields.io/badge/tools-24-blue)](#tools)\n"
    assert check_readme_counts.parse_claimed_tool_count(readme) == 24


def test_parse_claimed_tool_count_missing_claim_returns_none():
    assert check_readme_counts.parse_claimed_tool_count("# bus-mcp\n") is None


def test_parse_claimed_tool_count_ignores_unrelated_badges():
    readme = (
        "[![Tests](https://img.shields.io/badge/tests-273%20%28272%20passing%2C%201"
        "%20skipped%29-brightgreen)](#testing)\n"
        "[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)\n"
    )
    assert check_readme_counts.parse_claimed_tool_count(readme) is None


def test_readme_tool_badge_matches_the_live_registered_tool_count():
    """THE GATE. Counts the tools the server actually registers and compares
    that to the number the public README claims. Adding a tool without
    touching the badge goes red here."""
    import asyncio

    from bus_mcp.server import mcp

    actual = len(asyncio.run(mcp.list_tools()))
    claimed = check_readme_counts.parse_claimed_tool_count(
        _README_PATH.read_text(encoding="utf-8")
    )
    assert claimed is not None, "README has no Tools badge in the anchored phrasing"
    assert claimed == actual, (
        f"README claims {claimed} tools; the server registers {actual}"
    )


def test_readme_tools_table_has_a_row_for_every_registered_tool():
    """A COUNT IS NOT COVERAGE. The badge can match while a tool has no row --
    an undocumented tool on a public server. This checks the table itself."""
    import asyncio

    from bus_mcp.server import mcp

    readme = _README_PATH.read_text(encoding="utf-8")
    missing = [
        t.name for t in asyncio.run(mcp.list_tools()) if f"`{t.name}`" not in readme
    ]
    assert missing == [], f"tools with no README mention: {missing}"


def test_the_tool_count_gate_can_fail(tmp_path):
    """POSITIVE CONTROL for the gate above: a README claiming the wrong number
    must not parse as agreement."""
    readme = "[![Tools](https://img.shields.io/badge/tools-1-blue)](#tools)\n"
    assert check_readme_counts.parse_claimed_tool_count(readme) == 1
