"""RAILS PINS: the routes this server must NEVER wrap, enforced by grep over
its own source and by introspection over its own registered tool list.

Three bus routes are operator-authority, each for its own reason:

  POST /validations/{id}/decide  -- authenticated by X-Bus-Operator-Secret. A
      quorum this client can request and vote in, but cannot DECIDE, is the
      whole separation: an agent that could decide its own validation would be
      marking its own homework with the operator's pen.
  POST /tasks                    -- mints executable work; its `auto` class is
      operator-authenticated. Minting stays a CLI ritual against a staged file
      the operator has actually read.
  POST /tasks/sweep              -- terminally abandons OTHER claimants' rows.
      Not per-task, not reversible, not this client's.

A rule written in a docstring is a wish. These tests are what make it a rule:
two independent pins per route (source text AND registered surface), so
deleting one does not silently retire the constraint.

WHAT THE SOURCE PINS CANNOT SEE, named here rather than left to be discovered:
they are literal substring greps, so a path ASSEMBLED FROM VARIABLES
(`f"/validations/{id}/{action}"` with `action` built at runtime) slips every
one of them. The backstop is the exact-tool-set allowlist in
`test_server.py::test_no_unexpected_extra_tools` plus both README tool gates
in `test_check_readme_counts.py` -- a review's evasion probe tripped four of
those. That is a COVERAGE pin doing a RAILS pin's job, and a developer adding
the tool would naturally update the allowlist, so the residual risk is real
and is recorded rather than claimed away. Scope is stated with
the pattern, per the same discipline: the greps run over `bus_mcp/*.py` only
-- this file's own prose is deliberately outside the scanned set, which is
why it can name the forbidden paths at all.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from bus_mcp.server import mcp

_PKG = pathlib.Path(__file__).resolve().parent.parent / "bus_mcp"
_SOURCES = sorted(_PKG.glob("*.py"))


def _tool_names() -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_scope_the_pins_actually_read_source():
    """POSITIVE CONTROL for the greps themselves: prove the file set is
    non-empty and really contains this package's code. A grep over zero files
    passes every 'absent' assertion there is."""
    assert len(_SOURCES) >= 4
    joined = "".join(p.read_text(encoding="utf-8") for p in _SOURCES)
    assert "/validations" in joined, "the scan cannot see the module it guards"
    assert "/tasks/" in joined


@pytest.mark.parametrize("path", ["/decide", "/tasks/sweep"])
def test_forbidden_route_path_never_appears_in_source(path):
    for source in _SOURCES:
        text = source.read_text(encoding="utf-8")
        assert path not in text, f"{source.name} references the forbidden path {path}"


def test_task_minting_route_is_never_called():
    """`POST /tasks` exactly -- the minting route. The claim/heartbeat/finish
    paths are `/tasks/{id}/...` and are deliberately NOT matched by this: the
    pin is on the bare collection path, which is the one that mints."""
    for source in _SOURCES:
        text = source.read_text(encoding="utf-8")
        assert '"/tasks"' not in text, f"{source.name} calls the task-minting route"
        assert "'/tasks'" not in text


@pytest.mark.parametrize(
    "forbidden",
    ["decide", "decide_validation", "post_task", "mint_task", "sweep_tasks",
     "sweep_lanes", "create_task"],
)
def test_no_operator_authority_tool_is_registered(forbidden):
    assert forbidden not in _tool_names()


def test_no_registered_tool_name_contains_decide():
    assert [n for n in _tool_names() if "decide" in n.lower()] == []


def test_the_operator_secret_is_never_read_or_sent():
    """The bus's second secret. This server does not hold it, so it must not
    reference it by name either -- a header it cannot fill is a header it
    should not know how to send."""
    for source in _SOURCES:
        text = source.read_text(encoding="utf-8")
        assert "BUS_OPERATOR_SECRET" not in text
        assert "X-Bus-Operator-Secret:" not in text
        assert "os.environ.get(\"X-Bus-Operator-Secret\")" not in text


def test_the_write_secret_value_is_never_logged_or_returned():
    """`get_write_secret` exists to build one header. Nothing may print it,
    return it, or fold it into a result payload."""
    for source in _SOURCES:
        for i, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "get_write_secret" not in stripped:
                continue
            assert "print" not in stripped, f"{source.name}:{i} prints the write secret"
            assert "return" not in stripped, f"{source.name}:{i} returns the write secret"
