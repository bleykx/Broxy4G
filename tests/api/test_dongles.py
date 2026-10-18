import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.api.app import create_app
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.services.discovery import DiscoveredDongle
from broxy4g.services.proxy_health import ProxyHealthService


async def _enroll_online_via_app(app, imei, interface, table_id, fwmark) -> Dongle:
    """Enrôle un dongle via l'app, le passe en ONLINE avec modem+bearer prêts pour un reset DONE."""
    enrollment_service = app.state.enrollment_service
    dongle = (await enrollment_service.enroll(IdentityMaterial(imei=imei), "Test", "test.apn")).dongle
    dongle.state = DongleState.ONLINE
    dongle.interface_name = interface
    dongle.route_table_id = table_id
    dongle.fwmark = fwmark
    await enrollment_service.dongle_repo.save(dongle)
    app.state.modem.add_modem(ModemInfo(
        path=interface, imei=imei, iccid=None,
        usb_serial=None, state="connected", interface_name=interface,
    ))
    app.state.modem.set_bearer_ip(interface, BearerIpConfig(
        address="10.0.0.1", prefix=30, gateway="10.0.0.2"
    ))
    # Le gate de propagation d'IP publique rend DONE seulement si la sonde est OK.
    app.state.connectivity.set_public_ip("203.0.113.9")
    return dongle


@pytest.fixture
def mock_modem():
    return AsyncMock()


@pytest.fixture
def mock_dongle_repo():
    return AsyncMock()


@pytest.fixture
def mock_discovery_service():
    return AsyncMock()


@pytest.fixture
def test_app(mock_modem, mock_dongle_repo, mock_discovery_service):
    os.environ["BROXY4G_API_USER"] = "admin"
    os.environ["BROXY4G_API_PASSWORD"] = "test-pass"
    app = create_app(
        modem=mock_modem,
        dongle_repo=mock_dongle_repo,
        # We'll mock the discovery service in the app state
    )
    app.state.discovery_service = mock_discovery_service
    return TestClient(app)


class TestDonglesApi:
    def test_get_discovered_dongles_returns_correct_shape(self, test_app, mock_discovery_service, auth_headers):
        # Given
        discovered_dongle = DiscoveredDongle(
            identity_imei="123456789012345",
            identity_usb_serial="USB123",
            identity_iccid="ICCID123",
            interface_name="wwan0",
            suggested_label="Dongle 345",
            suggested_apn="free",
        )
        mock_discovery_service.list_unenrolled.return_value = [discovered_dongle]
        
        # When
        response = test_app.get("/api/v1/dongles/discovered", headers=auth_headers)
        
        # Then
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["identity_imei"] == "123456789012345"
        assert data[0]["identity_usb_serial"] == "USB123"
        assert data[0]["identity_iccid"] == "ICCID123"
        assert data[0]["interface_name"] == "wwan0"
        assert data[0]["suggested_label"] == "Dongle 345"
        assert data[0]["suggested_apn"] == "free"

    def test_list_dongles_returns_summary_metrics(self, test_app, mock_dongle_repo, auth_headers):
        dongle = Dongle(identity=IdentityMaterial(imei="123456789012345"), label="T")
        mock_dongle_repo.list_all.return_value = [dongle]
        test_app.app.state.metrics_snapshot.update(dongle.id, "1.2.3.4", 85)

        response = test_app.get("/api/v1/dongles", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["public_ip"] == "1.2.3.4"
        assert data[0]["signal_quality"] == 85


class TestEnrollApi:
    async def test_enroll_returns_proxy_credentials_once(self, client, auth_headers):
        payload = {"identity_imei": "123456789012345", "label": "Dongle 1234"}
        response = await client.post("/api/v1/dongles", json=payload, headers=auth_headers)

        assert response.status_code == 201
        data = response.json()
        creds = data["proxy_credentials"]
        assert creds is not None
        assert creds["username"]
        assert creds["password"]
        dongle_id = data["id"]

        # Les GET suivants ne renveront jamais les identifiants en clair.
        detail = (await client.get(f"/api/v1/dongles/{dongle_id}", headers=auth_headers)).json()
        assert "proxy_credentials" not in detail
        assert "password" not in detail

        proxy = (await client.get(f"/api/v1/dongles/{dongle_id}/proxy", headers=auth_headers)).json()
        assert proxy["auth_user"] == creds["username"]
        assert "password" not in proxy

    async def test_enroll_credentials_null_when_auto_create_disabled(self, client, auth_headers):
        await client.put(
            "/api/v1/settings/proxy-auto-create", json={"enabled": False}, headers=auth_headers
        )
        response = await client.post(
            "/api/v1/dongles",
            json={"identity_imei": "987654321098765", "label": "Dongle auto off"},
            headers=auth_headers,
        )

        assert response.status_code == 201
        assert response.json()["proxy_credentials"] is None


class TestResetApi:
    async def test_reset_single_returns_done(self, client, app, auth_headers):
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)

        response = await client.post(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["status"] == "DONE"

    async def test_reset_single_returns_pending_when_already_running(self, client, app, auth_headers):
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)
        app.state.reset_lock.acquire(str(dongle.id))  # un reset tourne déjà

        response = await client.post(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PENDING"
        assert body["message"] == "reset déjà en cours"

    async def test_reset_batch_returns_result_per_dongle(self, client, app, auth_headers):
        d1 = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)
        d2 = await _enroll_online_via_app(app, "987654321098765", "wwan1", 10002, 2)

        response = await client.post(
            "/api/v1/dongles/reset",
            json={"ids": [str(d1.id), str(d2.id)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        results = {r["dongle_id"]: r for r in response.json()["results"]}
        assert set(results) == {str(d1.id), str(d2.id)}
        assert results[str(d1.id)]["status"] == "DONE"
        assert results[str(d2.id)]["status"] == "DONE"

    async def test_reset_batch_pending_and_error(self, client, app, auth_headers):
        """Batch HTTP : DONE pour un dongle sain, PENDING pour un pré-verrouillé, ERROR pour un inconnu."""
        d1 = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)
        d2 = await _enroll_online_via_app(app, "987654321098765", "wwan1", 10002, 2)
        app.state.reset_lock.acquire(str(d2.id))  # d2 pré-verrouillé → PENDING
        unknown = uuid4()

        response = await client.post(
            "/api/v1/dongles/reset",
            json={"ids": [str(d1.id), str(d2.id), str(unknown)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        results = {r["dongle_id"]: r["status"] for r in response.json()["results"]}
        assert results[str(d1.id)] == "DONE"
        assert results[str(d2.id)] == "PENDING"
        assert results[str(unknown)] == "ERROR"

    async def test_reset_batch_rejects_empty_ids(self, client, auth_headers):
        response = await client.post(
            "/api/v1/dongles/reset", json={"ids": []}, headers=auth_headers
        )

        assert response.status_code == 422


class TestProxyHealthApi:
    async def test_detail_exposes_healthy_proxy(self, client, app, auth_headers):
        response = await client.post(
            "/api/v1/dongles",
            json={"identity_imei": "555666777888999", "label": "Dongle santé"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        dongle_id = response.json()["id"]

        # Le conteneur est "running" (FakeDockerCompose) mais aucun port n'est
        # réellement ouvert : on injecte un probe pour isoler le critère TCP.
        async def _reachable(host: str, port: int) -> bool:
            return True

        app.state.proxy_health_service = ProxyHealthService(
            app.state.proxy_repo,
            app.state.docker_compose,
            app.state.proxy_listen_service,
            probe=_reachable,
        )

        detail = (await client.get(f"/api/v1/dongles/{dongle_id}", headers=auth_headers)).json()
        assert detail["metrics"]["proxy_health"] == "healthy"
        assert detail["metrics"]["proxy_health_reason"] is None

    async def test_detail_exposes_unhealthy_when_port_not_reachable(self, client, app, auth_headers):
        response = await client.post(
            "/api/v1/dongles",
            json={"identity_imei": "333444555666777", "label": "Dongle injoignable"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        dongle_id = response.json()["id"]

        # Probe par défaut (vrai TCP sur 127.0.0.1) : FakeDockerCompose n'ouvre
        # pas de port → l'échec silencieux de publication est maintenant visible.
        detail = (await client.get(f"/api/v1/dongles/{dongle_id}", headers=auth_headers)).json()
        assert detail["metrics"]["proxy_health"] == "unhealthy"
        assert "non joignable" in detail["metrics"]["proxy_health_reason"]

    async def test_detail_exposes_unknown_when_no_proxy(self, client, app, auth_headers):
        await client.put(
            "/api/v1/settings/proxy-auto-create", json={"enabled": False}, headers=auth_headers
        )
        response = await client.post(
            "/api/v1/dongles",
            json={"identity_imei": "444555666777888", "label": "Dongle sans proxy"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        dongle_id = response.json()["id"]

        detail = (await client.get(f"/api/v1/dongles/{dongle_id}", headers=auth_headers)).json()
        assert detail["metrics"]["proxy_health"] == "unknown"
        assert detail["metrics"]["proxy_health_reason"] == "aucun proxy"