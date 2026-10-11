"""Tests du rendu de configuration 3proxy."""

import pytest

from broxy4g.domain.proxy_config import INTERNAL_PROXY_PORT, ProxyConfigSpec, render_3proxy_config


def _spec(**overrides) -> ProxyConfigSpec:
    defaults = dict(
        username="proxyuser",
        password_hash="$1$ab$hash",
        allowed_sources=("192.168.10.0/24", "10.0.0.5"),
    )
    defaults.update(overrides)
    return ProxyConfigSpec(**defaults)


def test_default_internal_port():
    assert INTERNAL_PROXY_PORT == 3128


def test_render_contains_required_directives():
    lines = render_3proxy_config(_spec()).splitlines()
    assert "auth strong" in lines
    assert 'users "proxyuser:CR:$1$ab$hash"' in lines
    assert "allow proxyuser 192.168.10.0/24,10.0.0.5 * * *" in lines
    assert "deny *" in lines
    assert "proxy -p3128" in lines
    assert "nserver 1.1.1.1" in lines
    assert "nserver 8.8.8.8" in lines
    assert "nscache 65536" in lines
    assert "maxconn 128" in lines


def test_render_ends_with_newline():
    assert render_3proxy_config(_spec()).endswith("\n")


def test_render_empty_sources_raises():
    with pytest.raises(ValueError):
        render_3proxy_config(_spec(allowed_sources=()))


def test_custom_listen_port_is_reflected():
    assert "proxy -p31205" in render_3proxy_config(_spec(listen_port=31205))


def test_custom_dns_and_max_connections_are_reflected():
    lines = render_3proxy_config(_spec(dns_servers=("9.9.9.9",), max_connections=64)).splitlines()
    assert sum(line.startswith("nserver ") for line in lines) == 1
    assert "nserver 9.9.9.9" in lines
    assert "maxconn 64" in lines
