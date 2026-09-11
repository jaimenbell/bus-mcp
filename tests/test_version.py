"""Version-parity pin.

The version is written in THREE places -- `pyproject.toml`, `server.json`
(twice: the package version and the published-package entry), and
`bus_mcp.__version__`. Three copies of one fact drift on one of them,
silently, and the MCP-registry entry is the copy nobody reads until a publish
goes out wrong. This test is what makes them one fact.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import bus_mcp

_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _server_json_versions() -> list[str]:
    data = json.loads((_ROOT / "server.json").read_text(encoding="utf-8"))
    versions = [data["version"]]
    versions += [p["version"] for p in data.get("packages", []) if "version" in p]
    return versions


def test_package_dunder_version_matches_pyproject():
    assert bus_mcp.__version__ == _pyproject_version()


def test_server_json_versions_match_pyproject():
    expected = _pyproject_version()
    actual = _server_json_versions()
    assert actual, "server.json declares no version at all"
    assert all(v == expected for v in actual), (
        f"server.json versions {actual} drifted from pyproject {expected}"
    )


def test_changelog_documents_the_current_version():
    """A release with no changelog entry is a release nobody can read."""
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {_pyproject_version()}" in changelog
