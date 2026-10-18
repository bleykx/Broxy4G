"""Route GET de reset de dongle (outils externes GET-only type Ankabot)."""

import logging
from uuid import uuid4

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial


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


class TestResetGet:
    async def test_get_reset_returns_done(self, client, app, auth_headers):
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.status_code == 200
        assert response.text == "DONE"
        assert response.headers["content-type"].startswith("text/plain")

    async def test_get_reset_unknown_dongle_returns_error(self, client, auth_headers):
        """Dongle inexistant : corps brut ``ERROR`` (HTTP 200), pas de JSON."""
        unknown_id = uuid4()

        response = await client.get(f"/api/v1/dongles/{unknown_id}/reset", headers=auth_headers)

        assert response.status_code == 200
        assert response.text == "ERROR"
        assert not response.text.lstrip().startswith(("{", "["))

    async def test_get_reset_requires_auth(self, client, app):
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset")

        assert response.status_code == 401

    async def test_get_reset_no_store_headers(self, client, app, auth_headers):
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"

    async def test_get_reset_pending_when_lock_held(self, client, app, auth_headers):
        """Le verrou existant s'applique aussi via GET : PENDING, pas de 2e reset."""
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)
        app.state.reset_lock.acquire(str(dongle.id))  # un reset tourne déjà

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.status_code == 200
        assert response.text == "PENDING"

    async def test_get_reset_logs_outcome_detail(self, client, app, auth_headers, caplog):
        """Le corps ne portant que le statut, la raison reste dans les logs."""
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)
        app.state.reset_lock.acquire(str(dongle.id))  # un reset tourne déjà

        with caplog.at_level(logging.INFO, logger="broxy4g.api.routers.dongle_reset"):
            response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.text == "PENDING"
        assert any(
            "PENDING" in r.getMessage() and "reset déjà en cours" in r.getMessage()
            for r in caplog.records
        ), "la raison du PENDING doit être journalisée puisque le corps ne porte que le statut"

    async def test_get_reset_within_cooldown_returns_pending(self, client, app, auth_headers):
        """Garde anti-tempête : un 2e GET dans le cooldown (défaut 60 s) renvoie PENDING."""
        dongle = await _enroll_online_via_app(app, "123456789012345", "wwan0", 10001, 1)

        first = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)
        second = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert first.status_code == 200
        assert first.text == "DONE"
        assert second.status_code == 200
        assert second.text == "PENDING"
