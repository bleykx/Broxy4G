"""Tests API des pools de proxies (authentification, CRUD, membres, secrets)."""

import json
from uuid import uuid4

import pytest

from broxy4g.api.auth import QUERY_AUTH_ENV
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxyState

POOLS = "/api/v1/pools"


async def _create_pool(client, auth_headers, label="Prod"):
    response = await client.post(POOLS, json={"label": label}, headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _save_proxy(app, **overrides) -> Proxy:
    """Persiste un proxy de test avec des valeurs par défaut sans mot de passe."""
    values = {
        "dongle_id": None,
        "container_name": "broxy4g-proxy-test",
        "listen_port": 31201,
        "state": ProxyState.STOPPED,
        "auth_user": "u",
        "password_encrypted": None,
    }
    values.update(overrides)
    proxy = Proxy(**values)
    await app.state.proxy_repo.save(proxy)
    return proxy


async def test_pools_require_auth(client):
    response = await client.get(POOLS)
    assert response.status_code == 401


async def test_list_pools_empty(client, auth_headers):
    response = await client.get(POOLS, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_create_pool_returns_password_once(client, auth_headers):
    body = await _create_pool(client, auth_headers)

    assert body["pool"]["listen_port"] == 31301
    assert body["pool"]["auth_user"]
    assert body["pool"]["enabled"] is True
    # Faux gateway : « à l'écoute » signifie enregistré, aucun socket réel.
    assert body["pool"]["listening"] is True
    assert body["pool"]["bind_ip"] == "127.0.0.1"
    assert body["pool"]["members"] == []
    assert body["pool"]["eligible_count"] == 0
    assert body["password"]

    listing = (await client.get(POOLS, headers=auth_headers)).json()
    assert len(listing) == 1
    assert "password" not in listing[0]


async def test_create_pool_blank_label_is_rejected(client, auth_headers):
    response = await client.post(POOLS, json={"label": "   "}, headers=auth_headers)
    assert response.status_code == 422


async def test_unknown_pool_is_not_found(client, auth_headers):
    missing = uuid4()
    assert (await client.get(f"{POOLS}/{missing}", headers=auth_headers)).status_code == 404
    response = await client.patch(
        f"{POOLS}/{missing}", json={"enabled": False}, headers=auth_headers
    )
    assert response.status_code == 404
    assert (await client.delete(f"{POOLS}/{missing}", headers=auth_headers)).status_code == 404
    response = await client.get(f"{POOLS}/{missing}/credentials", headers=auth_headers)
    assert response.status_code == 404


async def test_update_pool_label_and_enabled(client, auth_headers):
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]

    response = await client.patch(
        f"{POOLS}/{pool_id}", json={"label": "Renamed", "enabled": False}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["label"] == "Renamed"
    assert response.json()["enabled"] is False

    response = await client.patch(
        f"{POOLS}/{pool_id}", json={"enabled": True}, headers=auth_headers
    )
    assert response.json()["enabled"] is True
    assert response.json()["label"] == "Renamed"


async def test_credentials_are_not_cacheable(client, auth_headers):
    body = await _create_pool(client, auth_headers)
    pool_id = body["pool"]["id"]

    response = await client.get(f"{POOLS}/{pool_id}/credentials", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["password"] == body["password"]
    assert response.json()["pool"]["auth_user"] == body["pool"]["auth_user"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_add_member_with_unknown_proxy_is_not_found(client, auth_headers):
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]
    response = await client.post(
        f"{POOLS}/{pool_id}/members", json={"proxy_id": str(uuid4())}, headers=auth_headers
    )
    assert response.status_code == 404


async def test_member_lifecycle_and_reasons(app, client, auth_headers):
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]
    proxy = await _save_proxy(app)
    payload = {"proxy_id": str(proxy.id)}

    response = await client.post(f"{POOLS}/{pool_id}/members", json=payload, headers=auth_headers)
    assert response.status_code == 201, response.text
    members = response.json()["members"]
    assert len(members) == 1
    assert members[0]["proxy_id"] == str(proxy.id)
    assert members[0]["eligible"] is False
    assert members[0]["reason"] == "proxy non démarré (state=STOPPED)"
    assert members[0]["target"] is None
    assert response.json()["eligible_count"] == 0

    again = await client.post(f"{POOLS}/{pool_id}/members", json=payload, headers=auth_headers)
    assert again.status_code == 200
    assert len(again.json()["members"]) == 1

    status_response = await client.get(f"{POOLS}/{pool_id}/status", headers=auth_headers)
    assert status_response.status_code == 200
    assert status_response.json() == again.json()

    removed = await client.delete(
        f"{POOLS}/{pool_id}/members/{proxy.id}", headers=auth_headers
    )
    assert removed.status_code == 200
    assert removed.json()["members"] == []


async def test_eligible_member_exposes_target_without_secrets(app, client, auth_headers):
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]
    dongle = Dongle(
        identity=IdentityMaterial(imei="123456789012345"),
        label="D",
        state=DongleState.ONLINE,
    )
    await app.state.dongle_repo.save(dongle)
    encrypted = app.state.proxy_secret_box.encrypt("sesame")
    proxy = await _save_proxy(
        app,
        dongle_id=dongle.id,
        state=ProxyState.RUNNING,
        network_ip="172.28.0.5",
        auth_user="member",
        password_encrypted=encrypted,
    )

    response = await client.post(
        f"{POOLS}/{pool_id}/members", json={"proxy_id": str(proxy.id)}, headers=auth_headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    member = body["members"][0]
    assert member["eligible"] is True
    assert member["reason"] is None
    assert member["target"] == "172.28.0.5:3128"
    assert body["eligible_count"] == 1

    serialized = json.dumps(body)
    assert "sesame" not in serialized
    assert encrypted not in serialized


async def test_delete_pool(client, auth_headers):
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]

    response = await client.delete(f"{POOLS}/{pool_id}", headers=auth_headers)
    assert response.status_code == 204
    assert (await client.get(f"{POOLS}/{pool_id}", headers=auth_headers)).status_code == 404


@pytest.mark.parametrize("method", ["get", "post"])
async def test_pool_admin_routes_are_authenticated(client, method):
    if method == "get":
        response = await client.get(POOLS)
    else:
        response = await client.post(POOLS, json={"label": "Prod"})
    assert response.status_code == 401


async def _add_member(client, auth_headers, pool_id, proxy_id) -> None:
    response = await client.post(
        f"{POOLS}/{pool_id}/members", json={"proxy_id": str(proxy_id)}, headers=auth_headers
    )
    assert response.status_code in (200, 201), response.text


async def test_available_labels_is_plain_text_of_eligible_labels(
    app, client, auth_headers
):
    """``/available`` : labels des membres éligibles, un par ligne, rien d'autre."""
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]
    url = f"{POOLS}/{pool_id}/available"

    empty = await client.get(url, headers=auth_headers)
    assert empty.status_code == 200
    assert empty.text == ""
    assert empty.headers["content-type"].startswith("text/plain")
    assert empty.headers["cache-control"] == "no-store"

    dongle = Dongle(
        identity=IdentityMaterial(imei="123456789012345"),
        label="Dongle A",
        state=DongleState.ONLINE,
    )
    await app.state.dongle_repo.save(dongle)
    eligible = await _save_proxy(
        app,
        dongle_id=dongle.id,
        state=ProxyState.RUNNING,
        network_ip="172.28.0.5",
        auth_user="member",
        password_encrypted=app.state.proxy_secret_box.encrypt("sesame"),
    )
    stopped = await _save_proxy(app, listen_port=31202)
    await _add_member(client, auth_headers, pool_id, eligible.id)
    await _add_member(client, auth_headers, pool_id, stopped.id)

    response = await client.get(url, headers=auth_headers)
    assert response.status_code == 200
    assert response.text == "Dongle A"
    assert "sesame" not in response.text

    detail = (await client.get(f"{POOLS}/{pool_id}", headers=auth_headers)).json()
    labels = {m["label"] for m in detail["members"]}
    assert labels == {"Dongle A", None}


async def test_available_labels_requires_auth_and_known_pool(client, auth_headers):
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]
    assert (await client.get(f"{POOLS}/{pool_id}/available")).status_code == 401
    missing = await client.get(f"{POOLS}/{uuid4()}/available", headers=auth_headers)
    assert missing.status_code == 404


async def test_available_labels_accepts_query_auth(app, client, auth_headers, monkeypatch):
    """Outil GET-only : l'URL fonctionne avec ``?user=&password=`` (opt-in)."""
    monkeypatch.setenv(QUERY_AUTH_ENV, "true")
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]

    response = await client.get(f"{POOLS}/{pool_id}/available?user=admin&password=test-pass")

    assert response.status_code == 200
    assert response.text == ""
    assert response.headers["content-type"].startswith("text/plain")


async def test_available_labels_query_auth_disabled_by_default(client, auth_headers, monkeypatch):
    monkeypatch.delenv(QUERY_AUTH_ENV, raising=False)
    pool_id = (await _create_pool(client, auth_headers))["pool"]["id"]

    response = await client.get(f"{POOLS}/{pool_id}/available?user=admin&password=test-pass")

    assert response.status_code == 401
