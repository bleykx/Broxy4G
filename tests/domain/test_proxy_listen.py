from broxy4g.domain.proxy_listen import (
    ALLOWED_LISTEN_KINDS,
    InvalidListenInterface,
    is_listen_kind_allowed,
)


def test_allowed_listen_kinds():
    assert ALLOWED_LISTEN_KINDS == frozenset({"ethernet", "wifi"})


def test_is_listen_kind_allowed_true_for_ethernet_and_wifi():
    assert is_listen_kind_allowed("ethernet") is True
    assert is_listen_kind_allowed("wifi") is True


def test_is_listen_kind_allowed_false_for_other_wwan_loopback():
    assert is_listen_kind_allowed("other") is False
    assert is_listen_kind_allowed("wwan") is False
    assert is_listen_kind_allowed("loopback") is False
    assert is_listen_kind_allowed("docker") is False
    assert is_listen_kind_allowed("veth") is False


def test_invalid_listen_interface_is_a_value_error():
    assert issubclass(InvalidListenInterface, ValueError)
