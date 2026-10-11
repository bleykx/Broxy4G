import pytest

from broxy4g.domain.proxy_network import (
    DOCKER_GATEWAY,
    DOCKER_NETWORK_NAME,
    DOCKER_SUBNET,
    PROXY_PORT_END,
    PROXY_PORT_START,
    network_ip_for_port,
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
