"""Tests de normalisation/fusion des sources autorisées."""

import pytest

from broxy4g.domain.allowed_hosts import merge_allowed_sources, normalize_allowed_host


def test_normalize_ipv4():
    assert normalize_allowed_host("192.168.1.10") == "192.168.1.10"
    assert normalize_allowed_host(" 10.0.0.5 ") == "10.0.0.5"


def test_normalize_cidr_ipv4_non_strict():
    assert normalize_allowed_host("192.168.1.0/24") == "192.168.1.0/24"
    # strict=False : les bits d'hôte sont masqués par le réseau
    assert normalize_allowed_host("192.168.1.5/24") == "192.168.1.0/24"


def test_normalize_range():
    assert normalize_allowed_host("10.0.0.10-10.0.0.20") == "10.0.0.10-10.0.0.20"


def test_rejects_plain_text():
    with pytest.raises(ValueError):
        normalize_allowed_host("example.com")


def test_rejects_ipv6():
    with pytest.raises(ValueError):
        normalize_allowed_host("2001:db8::1")
    with pytest.raises(ValueError):
        normalize_allowed_host("2001:db8::/64")


def test_rejects_invalid_values():
    with pytest.raises(ValueError):
        normalize_allowed_host("999.1.1.1")
    with pytest.raises(ValueError):
        normalize_allowed_host("")
    with pytest.raises(ValueError):
        normalize_allowed_host("10.0.0.20-10.0.0.10")  # plage inversée


def test_merge_deduplicates_preserving_order():
    merged = merge_allowed_sources(
        ["192.168.1.0/24", "10.0.0.5"],
        ["10.0.0.5", "192.168.1.0/24", "172.16.0.0/16"],
    )
    assert merged == ["192.168.1.0/24", "10.0.0.5", "172.16.0.0/16"]


def test_merge_normalizes_entries():
    assert merge_allowed_sources([" 10.0.0.5/32 "]) == ["10.0.0.5/32"]


def test_merge_empty_groups():
    assert merge_allowed_sources() == []
    assert merge_allowed_sources([], ()) == []
