
import pytest


async def _enroll(client, auth_headers, imei="123456789012345"):
    payload = {"identity_imei": imei, "label": "Test"}
    resp = await client.post("/api/v1/dongles", json=payload, headers=auth_headers)
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_enroll_auto_creates_proxy(client, auth_headers):
    dongle_id = await _enroll(client, auth_headers)
    response = await client.get(f"/api/v1/dongles/{dongle_id}/proxy", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "dongle"
    assert data["state"] == "RUNNING"
    assert 31201 <= data["listen_port"] <= 31299
    assert "password" not in data


@pytest.mark.asyncio
async def test_create_proxy_when_auto_create_disabled(client, auth_headers):
    await client.put(
        "/api/v1/settings/proxy-auto-create", json={"enabled": False}, headers=auth_headers
    )
    dongle_id = await _enroll(client, auth_headers)
    assert (
        await client.get(f"/api/v1/dongles/{dongle_id}/proxy", headers=auth_headers)
    ).status_code == 404

    response = await client.post(
        f"/api/v1/dongles/{dongle_id}/proxy", json={"enabled": True}, headers=auth_headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["password"]
    assert body["proxy"]["state"] == "RUNNING"


@pytest.mark.asyncio
async def test_create_proxy_idempotent_returns_200_without_password(client, auth_headers, app):
    await client.put(
        "/api/v1/settings/proxy-auto-create", json={"enabled": False}, headers=auth_headers
    )
    dongle_id = await _enroll(client, auth_headers)

    first = await client.post(
        f"/api/v1/dongles/{dongle_id}/proxy", json={"enabled": True}, headers=auth_headers
    )
    assert first.status_code == 201
    assert first.json()["password"] is not None

    second = await client.post(
        f"/api/v1/dongles/{dongle_id}/proxy", json={"enabled": True}, headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["password"] is None

    listed = await client.get("/api/v1/proxies", headers=auth_headers)
    assert len(listed.json()) == 1

    # Pas de 2e démarrage de conteneur sur la requête idempotente.
    up_calls = sum(1 for c in app.state.docker_compose.calls if c[1] == "up")
    assert up_calls == 1


@pytest.mark.asyncio
async def test_stop_then_start_proxy(client, auth_headers):
    dongle_id = await _enroll(client, auth_headers)
    proxy_id = (
        await client.get(f"/api/v1/dongles/{dongle_id}/proxy", headers=auth_headers)
    ).json()["id"]

    await client.post(f"/api/v1/proxies/{proxy_id}/stop", headers=auth_headers)
    stopped = await client.get(f"/api/v1/proxies/{proxy_id}", headers=auth_headers)
    assert stopped.json()["state"] == "STOPPED"

    started = await client.post(f"/api/v1/proxies/{proxy_id}/start", headers=auth_headers)
    assert started.status_code == 202


@pytest.mark.asyncio
async def test_rotate_returns_password_once(client, auth_headers):
    dongle_id = await _enroll(client, auth_headers)
    proxy_id = (
        await client.get(f"/api/v1/dongles/{dongle_id}/proxy", headers=auth_headers)
    ).json()["id"]
    response = await client.post(f"/api/v1/proxies/{proxy_id}/rotate", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["password"]


@pytest.mark.asyncio
async def test_proxy_operations_require_auth(client, auth_headers):
    dongle_id = await _enroll(client, auth_headers, imei="999999999999999")
    response = await client.get(f"/api/v1/dongles/{dongle_id}/proxy")
    assert response.status_code == 401
