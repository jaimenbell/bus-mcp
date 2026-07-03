from __future__ import annotations

from bus_mcp import config


def test_default_base_url_when_unset(monkeypatch):
    monkeypatch.delenv("BUS_MCP_BASE_URL", raising=False)
    assert config.get_base_url() == config.DEFAULT_BASE_URL


def test_base_url_override(monkeypatch):
    monkeypatch.setenv("BUS_MCP_BASE_URL", "http://example.test:9000/api/bus/")
    # trailing slash stripped so path-joins in client.py don't double up
    assert config.get_base_url() == "http://example.test:9000/api/bus"


def test_default_timeout_when_unset(monkeypatch):
    monkeypatch.delenv("BUS_MCP_TIMEOUT_S", raising=False)
    assert config.get_timeout_s() == config.DEFAULT_TIMEOUT_S


def test_timeout_override(monkeypatch):
    monkeypatch.setenv("BUS_MCP_TIMEOUT_S", "3.5")
    assert config.get_timeout_s() == 3.5


def test_timeout_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("BUS_MCP_TIMEOUT_S", "not-a-number")
    assert config.get_timeout_s() == config.DEFAULT_TIMEOUT_S


def test_live_smoke_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BUS_MCP_LIVE", raising=False)
    assert config.is_live_smoke_enabled() is False


def test_live_smoke_enabled_when_set(monkeypatch):
    monkeypatch.setenv("BUS_MCP_LIVE", "1")
    assert config.is_live_smoke_enabled() is True


def test_live_smoke_disabled_for_non_1_value(monkeypatch):
    monkeypatch.setenv("BUS_MCP_LIVE", "true")
    assert config.is_live_smoke_enabled() is False
