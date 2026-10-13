import pytest

from broxy4g.adapters.local_uplink import LocalInterface


def _set_eth0(app, ip="192.168.1.10"):
    app.state.local_uplink.set_interfaces([LocalInterface(name="eth0", ip=ip, kind="ethernet")])
    app.state.local_uplink.set_gateway("eth0", "192.168.1.1")
    app.state.local_uplink.set_link_state("eth0", "up")
    app.state.local_uplink.set_dns("eth0", ["192.168.1.1", "8.8.8.8"])


@pytest.mark.asyncio
async def test_list_local_connections_empty(client, auth_headers):
    response = await client.get("/api/v1/local-connections", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_enroll_local_connection_creates_pinned_proxy(app, client, auth_headers):
    _set_eth0(app)

    response = await client.post(
        "/api/v1/local-connections", json={"interface_name": "eth0"}, headers=auth_headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["password"]
    assert body["proxy"]["source_type"] == "local"
    assert body["proxy"]["interface_name"] == "eth0"

    listing = (await client.get("/api/v1/local-connections", headers=auth_headers)).json()
    assert listing[0]["enrolled"] is True
    assert listing[0]["proxy_id"] == body["proxy"]["id"]


@pytest.mark.asyncio
async def test_enroll_unknown_interface_returns_422(app, client, auth_headers):
    response = await client.post(
        "/api/v1/local-connections", json={"interface_name": "eth9"}, headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unenroll_local_connection_removes_proxy(app, client, auth_headers):
    _set_eth0(app)
    await client.post(
        "/api/v1/local-connections", json={"interface_name": "eth0"}, headers=auth_headers
    )

    response = await client.delete("/api/v1/local-connections/eth0", headers=auth_headers)

    assert response.status_code == 204
    listing = (await client.get("/api/v1/local-connections", headers=auth_headers)).json()
    assert listing[0]["enrolled"] is False
    assert listing[0]["proxy_id"] is None


@pytest.mark.asyncio
async def test_get_local_connection_detail_without_proxy(app, client, auth_headers):
    _set_eth0(app)

    response = await client.get("/api/v1/local-connections/eth0", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["interface_name"] == "eth0"
    assert body["ip"] == "192.168.1.10"
    assert body["kind"] == "ethernet"
    assert body["gateway"] == "192.168.1.1"
    assert body["link_state"] == "up"
    assert body["dns"] == ["192.168.1.1", "8.8.8.8"]
    assert body["enrolled"] is False
    assert body["proxy_id"] is None
    assert body["proxy"] is None
    assert body["proxy_health"] is None
    assert body["proxy_health_reason"] is None


@pytest.mark.asyncio
async def test_get_local_connection_detail_with_enrolled_proxy(app, client, auth_headers):
    _set_eth0(app)
    created = (
        await client.post(
            "/api/v1/local-connections", json={"interface_name": "eth0"}, headers=auth_headers
        )
    ).json()
    # Arrêt forcé du conteneur (fake) : santé déterministe, sans probe réseau réelle.
    app.state.docker_compose.set_state(created["proxy"]["container_name"], "exited")

    response = await client.get("/api/v1/local-connections/eth0", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["enrolled"] is True
    assert body["proxy_id"] == body["proxy"]["id"]
    assert body["proxy"]["source_type"] == "local"
    assert body["proxy"]["interface_name"] == "eth0"
    assert body["proxy_health"] == "unhealthy"
    assert "conteneur" in body["proxy_health_reason"]


@pytest.mark.asyncio
async def test_get_local_connection_detail_unknown_interface_404(app, client, auth_headers):
    _set_eth0(app)

    response = await client.get("/api/v1/local-connections/eth9", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "interface inconnue: eth9"


@pytest.mark.asyncio
async def test_list_local_connections_includes_gateway_and_link_state(app, client, auth_headers):
    _set_eth0(app)

    response = await client.get("/api/v1/local-connections", headers=auth_headers)

    assert response.status_code == 200
    item = response.json()[0]
    assert item["gateway"] == "192.168.1.1"
    assert item["link_state"] == "up"


@pytest.mark.asyncio
async def test_local_connections_require_auth(client):
    assert (await client.get("/api/v1/local-connections")).status_code == 401
