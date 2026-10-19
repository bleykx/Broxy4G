"""Route GET de reset de dongle (outils externes GET-only type Ankabot).

Couvre aussi le paramètre ``?lock=`` (verrou manuel post-DONE) et la route
``GET /dongles/{id}/unlock`` (déverrouillage idempotent).
"""

import logging
from uuid import uuid4

import pytest


class TestResetGet:
    async def test_get_reset_returns_done(self, client, app, auth_headers, enroll_online):
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

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

    async def test_get_reset_requires_auth(self, client, app, enroll_online):
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset")

        assert response.status_code == 401

    async def test_get_reset_no_store_headers(self, client, app, auth_headers, enroll_online):
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"

    async def test_get_reset_pending_when_lock_held(self, client, app, auth_headers, enroll_online):
        """Le verrou existant s'applique aussi via GET : PENDING, pas de 2e reset."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)
        app.state.reset_lock.acquire(str(dongle.id))  # un reset tourne déjà

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.status_code == 200
        assert response.text == "PENDING"

    async def test_get_reset_logs_outcome_detail(self, client, app, auth_headers, enroll_online, caplog):
        """Le corps ne portant que le statut, la raison reste dans les logs."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)
        app.state.reset_lock.acquire(str(dongle.id))  # un reset tourne déjà

        with caplog.at_level(logging.INFO, logger="broxy4g.api.routers.dongle_reset"):
            response = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert response.text == "PENDING"
        assert any(
            "PENDING" in r.getMessage() and "reset déjà en cours" in r.getMessage()
            for r in caplog.records
        ), "la raison du PENDING doit être journalisée puisque le corps ne porte que le statut"

    async def test_get_reset_within_cooldown_returns_pending(self, client, app, auth_headers, enroll_online):
        """Garde anti-tempête : un 2e GET dans le cooldown (défaut 60 s) renvoie PENDING."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

        first = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)
        second = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert first.status_code == 200
        assert first.text == "DONE"
        assert second.status_code == 200
        assert second.text == "PENDING"

    async def test_get_reset_with_lock_arms_lockout(self, client, app, auth_headers, enroll_online):
        """``?lock=120`` : DONE arme le verrou manuel ; le 2e GET renvoie LOCK (corps brut)."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

        first = await client.get(f"/api/v1/dongles/{dongle.id}/reset?lock=120", headers=auth_headers)

        assert first.status_code == 200
        assert first.text == "DONE"
        assert app.state.reset_lockout.is_locked(str(dongle.id))

        second = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)

        assert second.status_code == 200
        assert second.text == "LOCK"

    async def test_manual_lock_then_unlock_then_reset(self, client, app, auth_headers, enroll_online):
        """Lock posé manuellement → LOCK, unlock → DONE (no-store), reset → DONE."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)
        app.state.reset_lockout.lock(str(dongle.id), 120.0)

        locked = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)
        assert locked.status_code == 200
        assert locked.text == "LOCK"

        unlock = await client.get(f"/api/v1/dongles/{dongle.id}/unlock", headers=auth_headers)
        assert unlock.status_code == 200
        assert unlock.text == "DONE"
        assert unlock.headers.get("Cache-Control") == "no-store"
        assert not app.state.reset_lockout.is_locked(str(dongle.id))

        after = await client.get(f"/api/v1/dongles/{dongle.id}/reset", headers=auth_headers)
        assert after.status_code == 200
        assert after.text == "DONE"

    async def test_unlock_unknown_dongle_returns_error(self, client, auth_headers):
        """Dongle inconnu : corps brut ``ERROR`` HTTP 200 (cohérent avec le reset GET)."""
        response = await client.get(f"/api/v1/dongles/{uuid4()}/unlock", headers=auth_headers)

        assert response.status_code == 200
        assert response.text == "ERROR"

    async def test_unlock_requires_auth(self, client, app, enroll_online):
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/unlock")

        assert response.status_code == 401

    @pytest.mark.parametrize("value", ["abc", "-1", "86401"])
    async def test_get_reset_invalid_lock_param_returns_400(
        self, client, app, auth_headers, enroll_online, value
    ):
        """``lock`` invalide (non entier, négatif ou > 86400) : 400, pas de reset."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)

        response = await client.get(f"/api/v1/dongles/{dongle.id}/reset?lock={value}", headers=auth_headers)

        assert response.status_code == 400
        assert not app.state.reset_lockout.is_locked(str(dongle.id))

    async def test_get_reset_unknown_dongle_with_lock_does_not_arm(self, client, app, auth_headers):
        """Dongle inconnu + ``lock`` : ERROR, et aucun lock armé."""
        unknown_id = uuid4()

        response = await client.get(f"/api/v1/dongles/{unknown_id}/reset?lock=120", headers=auth_headers)

        assert response.status_code == 200
        assert response.text == "ERROR"
        assert not app.state.reset_lockout.is_locked(str(unknown_id))
