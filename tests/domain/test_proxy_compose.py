from broxy4g.domain.proxy_compose import ComposeSpec, render_docker_compose


def _spec(**overrides) -> ComposeSpec:
    base = {
        "project_name": "broxy4g-proxy-abc",
        "container_name": "broxy4g-proxy-abc",
        "listen_port": 31201,
        "cfg_path": "/var/lib/broxy4g/proxies/abc/3proxy.cfg",
        "network_ip": "172.28.0.101",
    }
    base.update(overrides)
    return ComposeSpec(**base)


def test_render_contains_key_fields():
    text = render_docker_compose(_spec())
    assert "name: broxy4g-proxy-abc" in text
    assert "container_name: broxy4g-proxy-abc" in text
    assert '"0.0.0.0:31201:3128"' in text
    assert '"/var/lib/broxy4g/proxies/abc/3proxy.cfg:/etc/3proxy/3proxy.cfg:ro"' in text
    assert "ipv4_address: 172.28.0.101" in text
    assert "subnet: 172.28.0.0/16" in text
    assert text.endswith("\n")


def test_render_reflects_custom_port_and_ip():
    text = render_docker_compose(_spec(listen_port=31299, network_ip="172.28.0.199"))
    assert "0.0.0.0:31299:3128" in text
    assert "ipv4_address: 172.28.0.199" in text


def test_render_custom_bind_ip():
    text = render_docker_compose(_spec(bind_ip="192.168.1.10"))
    assert '"192.168.1.10:31201:3128"' in text
    assert "0.0.0.0:31201:3128" not in text


def test_render_omits_ports_when_bind_ip_none():
    text = render_docker_compose(_spec(bind_ip=None))
    assert "ports:" not in text
    assert "31201:3128" not in text
