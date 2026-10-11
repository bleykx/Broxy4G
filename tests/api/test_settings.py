"""Routes /settings/proxy-listen-interface : GET/PUT de l'interface d'écoute.

Crée une app locale (conftest partagé non modifié) avec un FakeLocalUplink peuplé.
"""

import os

import pytest

from broxy4g.adapters.local_uplink import FakeLocalUplink, LocalInterface
from broxy4g.api.app import create_app
from broxy4g.repositories.database import create_engine, create_session_factory, init_db
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository


@pytest.fixture
def app():
    os.environ["BROXY4G_API_USER"] = "admin"
    os.environ["BROXY4G_API_PASSWORD"] = "test-pass"
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sf = create_session_factory(engine)
    local_uplink = FakeLocalUplink()
    local_uplink.set_interfaces(
        [
            LocalInterface(name="eth0", ip="192.168.1.10", kind="ethernet"),
            LocalInterface(name="wlan0", ip="192.168.1.20", kind="wifi"),
            LocalInterface(name="other0", ip="10.0.0.5", kind="other"),
        ]
    )
    return create_app(
        dongle_repo=DongleRepository(sf),
        proxy_repo=ProxyRepository(sf),
        local_uplink=local_uplink,
    )


async def test_get_proxy_listen_interface_default(client, auth_headers):
    response = await client.get("/api/v1/settings/proxy-listen-interface", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"interface_name": None}


async def test_put_proxy_listen_interface_eth0_then_get_reflects(client, auth_headers):
    response = await client.put(
        "/api/v1/settings/proxy-listen-interface",
        json={"interface_name": "eth0"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == {"interface_name": "eth0"}

    listing = await client.get("/api/v1/settings/proxy-listen-interface", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == {"interface_name": "eth0"}


async def test_put_proxy_listen_interface_disallowed_kind_returns_422(client, auth_headers):
    response = await client.put(
        "/api/v1/settings/proxy-listen-interface",
        json={"interface_name": "other0"},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_put_proxy_listen_interface_unknown_returns_422(client, auth_headers):
    response = await client.put(
        "/api/v1/settings/proxy-listen-interface",
        json={"interface_name": "wwan0"},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_put_proxy_listen_interface_requires_auth(client):
    response = await client.put(
        "/api/v1/settings/proxy-listen-interface",
        json={"interface_name": "eth0"},
    )
    assert response.status_code == 401
