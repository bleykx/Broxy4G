import pytest

from broxy4g.domain.proxy_network import (
    DOCKER_GATEWAY,
    DOCKER_NETWORK_NAME,
    DOCKER_SUBNET,
    PROXY_PORT_END,
    PROXY_PORT_START,
    network_ip_for_port,
    port_for_ip,
)


def test_network_constants():
    assert DOCKER_NETWORK_NAME == "broxy4g-net"
    assert DOCKER_SUBNET == "172.28.0.0/16"
    assert DOCKER_GATEWAY == "172.28.0.1"


def test_first_and_last_port_map_to_expected_ip():
    assert network_ip_for_port(PROXY_PORT_START) == "172.28.0.101"
    assert network_ip_for_port(PROXY_PORT_END) == "172.28.0.199"


def test_ips_are_unique_across_range():
    ips = {network_ip_for_port(port) for port in range(PROXY_PORT_START, PROXY_PORT_END + 1)}
    assert len(ips) == PROXY_PORT_END - PROXY_PORT_START + 1


def test_out_of_range_raises():
    with pytest.raises(ValueError):
        network_ip_for_port(PROXY_PORT_START - 1)
    with pytest.raises(ValueError):
        network_ip_for_port(PROXY_PORT_END + 1)


def test_port_for_ip_round_trips_every_port():
    for port in range(PROXY_PORT_START, PROXY_PORT_END + 1):
        assert port_for_ip(network_ip_for_port(port)) == port


def test_port_for_ip_ignores_cidr_prefix():
    # Le format Docker inspect renvoie "172.28.0.101/16".
    assert port_for_ip("172.28.0.101/16") == PROXY_PORT_START
    assert port_for_ip("172.28.0.199/16") == PROXY_PORT_END


def test_port_for_ip_first_and_last():
    assert port_for_ip("172.28.0.101") == PROXY_PORT_START
    assert port_for_ip("172.28.0.199") == PROXY_PORT_END


@pytest.mark.parametrize(
    "ip",
    [
        "172.28.0.1",      # passerelle
        "172.28.0.100",    # avant la première IP proxy
        "172.28.0.200",    # après la dernière IP proxy
        "172.29.0.101",    # autre sous-réseau
        "192.168.1.40",    # IP hôte
        "not-an-ip",
        "172.28.0.",
    ],
)
def test_port_for_ip_returns_none_outside_scheme(ip):
    assert port_for_ip(ip) is None
