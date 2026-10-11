import pytest

from broxy4g.adapters.local_uplink import LocalInterface


def _set_eth0(app, ip="192.168.1.10"):
    app.state.local_uplink.set_interfaces([LocalInterface(name="eth0", ip=ip, kind="ethernet")])
    app.state.local_uplink.set_gateway("eth0", "192.168.1.1")


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
async def test_local_connections_require_auth(client):
    assert (await client.get("/api/v1/local-connections")).status_code == 401
