import pytest


async def _enroll(client, auth_headers, imei="123456789012345"):
    payload = {"identity_imei": imei, "label": "Test"}
    resp = await client.post("/api/v1/dongles", json=payload, headers=auth_headers)
    dongle_id = resp.json()["id"]
    proxy = await client.get(f"/api/v1/dongles/{dongle_id}/proxy", headers=auth_headers)
    return proxy.json()["id"]


@pytest.mark.asyncio
async def test_add_global_host_returns_201(client, auth_headers):
    response = await client.post(
        "/api/v1/allowed-hosts", json={"host": "203.0.113.5"}, headers=auth_headers
    )
    assert response.status_code == 201
    assert response.json() == {"host": "203.0.113.5"}
    listed = await client.get("/api/v1/allowed-hosts", headers=auth_headers)
    assert listed.json() == [{"host": "203.0.113.5"}]


@pytest.mark.asyncio
async def test_remove_global_host_returns_204(client, auth_headers):
    await client.post(
        "/api/v1/allowed-hosts", json={"host": "203.0.113.5"}, headers=auth_headers
    )
    response = await client.delete(
        "/api/v1/allowed-hosts", params={"host": "203.0.113.5"}, headers=auth_headers
    )
    assert response.status_code == 204
    listed = await client.get("/api/v1/allowed-hosts", headers=auth_headers)
    assert listed.json() == []


@pytest.mark.asyncio
async def test_add_global_empty_host_returns_422(client, auth_headers):
    response = await client.post(
        "/api/v1/allowed-hosts", json={"host": "   "}, headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_global_invalid_format_returns_422(client, auth_headers):
    for invalid in ("abc", "999.1.1.1", "::1", "10.0.0.9-10.0.0.1"):
        response = await client.post(
            "/api/v1/allowed-hosts", json={"host": invalid}, headers=auth_headers
        )
        assert response.status_code == 422, invalid
    assert (await client.get("/api/v1/allowed-hosts", headers=auth_headers)).json() == []


@pytest.mark.asyncio
async def test_add_global_invalid_format_not_persisted(client, auth_headers):
    response = await client.post(
        "/api/v1/allowed-hosts", json={"host": "999.1.1.1"}, headers=auth_headers
    )
    assert response.status_code == 422
    listed = await client.get("/api/v1/allowed-hosts", headers=auth_headers)
    assert listed.json() == []


@pytest.mark.asyncio
async def test_add_proxy_invalid_format_returns_422(client, auth_headers):
    proxy_id = await _enroll(client, auth_headers)
    for invalid in ("not-an-ip", "::1", "10.0.0.9-10.0.0.1"):
        response = await client.post(
            f"/api/v1/proxies/{proxy_id}/allowed-hosts",
            json={"host": invalid},
            headers=auth_headers,
        )
        assert response.status_code == 422, invalid
    listed = await client.get(
        f"/api/v1/proxies/{proxy_id}/allowed-hosts", headers=auth_headers
    )
    assert listed.json() == []


@pytest.mark.asyncio
async def test_allowed_hosts_require_auth(client, auth_headers):
    response = await client.get("/api/v1/allowed-hosts")
    assert response.status_code == 401
    response = await client.post("/api/v1/allowed-hosts", json={"host": "1.2.3.4"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_add_and_remove_proxy_host(client, auth_headers):
    proxy_id = await _enroll(client, auth_headers)

    added = await client.post(
        f"/api/v1/proxies/{proxy_id}/allowed-hosts",
        json={"host": "198.51.100.7"},
        headers=auth_headers,
    )
    assert added.status_code == 201
    assert added.json() == {"host": "198.51.100.7"}
    listed = await client.get(
        f"/api/v1/proxies/{proxy_id}/allowed-hosts", headers=auth_headers
    )
    assert listed.json() == [{"host": "198.51.100.7"}]

    removed = await client.delete(
        f"/api/v1/proxies/{proxy_id}/allowed-hosts",
        params={"host": "198.51.100.7"},
        headers=auth_headers,
    )
    assert removed.status_code == 204
    listed = await client.get(
        f"/api/v1/proxies/{proxy_id}/allowed-hosts", headers=auth_headers
    )
    assert listed.json() == []
