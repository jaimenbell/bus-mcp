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
# parse_claimed_counts -- anchored to the real README badge SHAPE:
#   [![Tests](https://img.shields.io/badge/tests-<N>%20%28<P>%20passing%2C%20<S>%20skipped%29-brightgreen)](#testing)
# i.e. a shields.io badge URL whose label text is "tests-<N> (<P> passing,
# <S> skipped)-brightgreen", percent-encoded.
#
# THE FIXTURES BELOW USE SYNTHETIC NUMBERS ON PURPOSE. A parser test needs a
# self-consistent input, not the suite's current count -- pinning the real
# count here would create a second copy of it that nothing checks, in the one
# file whose entire job is preventing exactly that. The real count is
# compared against the real README by the CI gate and, for tools, by
# test_readme_tool_badge_matches_the_live_registered_tool_count below.
# ---------------------------------------------------------------------------

def test_parse_claimed_counts_matches_real_badge_phrasing():
    readme = textwrap.dedent(
        """
        # bus-mcp

        [![Tests](https://img.shields.io/badge/tests-7%20%286%20passing%2C%201%20skipped%29-brightgreen)](#testing)
        """
    )
    claim = check_readme_counts.parse_claimed_counts(readme)
    assert claim == check_readme_counts.Counts(total=7, passed=6, skipped=1)


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

def _make_junit(total, skipped=0, failures=0, errors=0, name="pytest"):
    """Build a junitxml fixture whose <testcase> ELEMENTS actually match the
    root <testsuite> summary attributes (skipped/failures/errors first, the
    rest plain passes) -- so a fixture used to test the "claim matches
    reality" path is not itself an instance of the root-attribute-lies bug
    this lane is fixing. `_JUNIT_MISMATCHED_ROOT_COUNT` below is the one
    fixture that deliberately disagrees."""
    cases = []
    i = 0
    for _ in range(skipped):
        cases.append(f'<testcase classname="c" name="skip{i}" time="0.0"><skipped/></testcase>')
        i += 1
    for _ in range(failures):
        cases.append(f'<testcase classname="c" name="fail{i}" time="0.0"><failure message="x"/></testcase>')
        i += 1
    for _ in range(errors):
        cases.append(f'<testcase classname="c" name="err{i}" time="0.0"><error message="x"/></testcase>')
        i += 1
    while i < total:
        cases.append(f'<testcase classname="c" name="pass{i}" time="0.0"/>')
        i += 1
    body = "\n".join(cases)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<testsuites>\n"
        f'<testsuite name="{name}" errors="{errors}" failures="{failures}" '
        f'skipped="{skipped}" tests="{total}" time="7.9">\n'
        f"{body}\n"
        "</testsuite>\n"
        "</testsuites>\n"
    )


_JUNIT_MATCH = _make_junit(89, skipped=1)
_JUNIT_WITH_FAILURES = _make_junit(89, skipped=1, failures=3)

# THE POSITIVE-CONTROL FIXTURE for the root-attribute-vs-element-count fix:
# the root <testsuite tests="5"> attribute LIES -- only 3 real <testcase>
# elements exist in the body. A gate that trusts the attribute reports 5; a
# gate that counts elements (the fix) reports 3.
_JUNIT_MISMATCHED_ROOT_COUNT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="5" time="1.0">
<testcase classname="c" name="t1" time="0.0"/>
<testcase classname="c" name="t2" time="0.0"/>
<testcase classname="c" name="t3" time="0.0"/>
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


def test_parse_actual_counts_counts_testcase_elements_not_the_root_attr(tmp_path):
    """POSITIVE CONTROL, half 1 (FIRES on the bug): the root <testsuite
    tests="5"> attribute is wrong -- only 3 <testcase> elements are actually
    in the file. The gate must report the real element count (3), never the
    attribute (5). This is the CLAUDE.md rule 'the root tests= attr counts
    SUBTESTS' made concrete: the attribute is pytest's own bookkeeping and
    can drift from what the file body actually contains."""
    junit_file = tmp_path / "junit.xml"
    junit_file.write_text(_JUNIT_MISMATCHED_ROOT_COUNT, encoding="utf-8")
    actual = check_readme_counts.parse_actual_counts(junit_file)
    assert actual.total == 3
    assert actual.passed == 3
    assert actual.skipped == 0


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
    # The claimed number stays deliberately WRONG and deliberately NOT the
    # suite's real count: a drift fixture pointed at the current number
    # becomes a no-op the day the suite happens to reach it.
    readme = tmp_path / "README.md"
    readme.write_text(
        "[![Tests](https://img.shields.io/badge/tests-63%20%2862%20passing%2C%201%20skipped%29-brightgreen)](#testing)\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT_MATCH, encoding="utf-8")

    rc = check_readme_counts.main(["--readme", str(readme), "--junit-xml", str(junit)])
    assert rc != 0


def test_the_root_attr_gate_fires_on_a_readme_that_trusts_the_lying_root_count(tmp_path):
    """POSITIVE CONTROL, half 2 (FIRES): a README claiming the root
    attribute's number (5, wrong) against the mismatched fixture (3 real
    <testcase> elements) must be flagged as drift -- rc != 0."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "[![Tests](https://img.shields.io/badge/tests-5%20%285%20passing%2C%200%20skipped%29-brightgreen)](#testing)\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT_MISMATCHED_ROOT_COUNT, encoding="utf-8")
    rc = check_readme_counts.main(["--readme", str(readme), "--junit-xml", str(junit)])
    assert rc != 0


def test_the_root_attr_gate_stays_silent_on_a_readme_matching_the_real_element_count(tmp_path):
    """POSITIVE CONTROL, half 2 (STAYS SILENT): a README claiming the real
    element count (3) against the same mismatched-root fixture must pass --
    the gate counts elements, so this is agreement, not drift."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "[![Tests](https://img.shields.io/badge/tests-3%20%283%20passing%2C%200%20skipped%29-brightgreen)](#testing)\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT_MISMATCHED_ROOT_COUNT, encoding="utf-8")
    rc = check_readme_counts.main(["--readme", str(readme), "--junit-xml", str(junit)])
    assert rc == 0


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
