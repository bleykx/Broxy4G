import base64

import pytest

from broxy4g.api.auth import QUERY_AUTH_ENV


def _basic_header(user: str, password: str) -> dict:
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


class TestQueryAuthEnabled:
    @pytest.fixture
    def query_auth(self, monkeypatch):
        monkeypatch.setenv(QUERY_AUTH_ENV, "true")

    async def test_valid_query_credentials(self, client, query_auth):
        response = await client.get("/api/v1/dongles?user=admin&password=test-pass")
        assert response.status_code == 200

    @pytest.mark.parametrize("value", ["1", "TRUE", "Yes", "On"])
    async def test_any_truish_value_enables(self, client, monkeypatch, value):
        monkeypatch.setenv(QUERY_AUTH_ENV, value)
        response = await client.get("/api/v1/dongles?user=admin&password=test-pass")
        assert response.status_code == 200

    async def test_wrong_password_in_query(self, client, query_auth):
        response = await client.get("/api/v1/dongles?user=admin&password=wrong")
        assert response.status_code == 401

    async def test_missing_password_param(self, client, query_auth):
        response = await client.get("/api/v1/dongles?user=admin")
        assert response.status_code == 401

    async def test_reset_query_auth_with_lock_then_unlock(self, client, app, query_auth, enroll_online):
        """Bout en bout : reset GET via query auth + ``lock``, LOCK au 2e appel, unlock."""
        dongle = await enroll_online("123456789012345", "wwan0", 10001, 1)
        reset_url = f"/api/v1/dongles/{dongle.id}/reset?user=admin&password=test-pass&lock=5"

        first = await client.get(reset_url)

        assert first.status_code == 200
        assert first.text == "DONE"

        second = await client.get(reset_url)

        assert second.status_code == 200
        assert second.text == "LOCK"

        unlock = await client.get(
            f"/api/v1/dongles/{dongle.id}/unlock?user=admin&password=test-pass"
        )

        assert unlock.status_code == 200
        assert unlock.text == "DONE"
        assert not app.state.reset_lockout.is_locked(str(dongle.id))

    async def test_malformed_header_not_fallback_to_query(self, client, query_auth):
        # L'en-tête présent (même mal formé) fait autorité : pas de fallback query.
        response = await client.get(
            "/api/v1/dongles?user=admin&password=test-pass",
            headers={"Authorization": "Basic !!!not-base64!!!"},
        )
        assert response.status_code == 401


class TestQueryAuthDisabled:
    @pytest.fixture(autouse=True)
    def disabled(self, monkeypatch):
        monkeypatch.delenv(QUERY_AUTH_ENV, raising=False)

    async def test_valid_query_credentials_rejected(self, client):
        response = await client.get("/api/v1/dongles?user=admin&password=test-pass")
        assert response.status_code == 401

    async def test_basic_header_still_works(self, client, auth_headers):
        response = await client.get("/api/v1/dongles", headers=auth_headers)
        assert response.status_code == 200

    async def test_header_takes_precedence_when_env_not_set(self, client):
        response = await client.get(
            "/api/v1/dongles?user=admin&password=test-pass", headers=_basic_header("admin", "test-pass")
        )
        assert response.status_code == 200

    async def test_401_without_www_authenticate(self, client):
        response = await client.get("/api/v1/dongles?user=admin&password=test-pass")
        assert response.status_code == 401
        # Pas de challenge WWW-Authenticate : il déclenche la popup native du navigateur.
        assert "www-authenticate" not in response.headers
