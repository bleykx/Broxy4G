"""Route /api/v1/session : vérification de session authentifiée.

Point de contrôle des identifiants utilisé par le frontend (le /health
public ne vérifie rien et ne doit pas servir à cela).
"""

import base64

import pytest


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic " + base64.b64encode(b"admin:not-the-pass").decode()},
        {"Authorization": "Basic " + base64.b64encode(b"wrong:test-pass").decode()},
    ],
    ids=["no-auth-header", "wrong-password", "wrong-user"],
)
async def test_session_rejects_missing_or_wrong_credentials(client, headers):
    response = await client.get("/api/v1/session", headers=headers)
    assert response.status_code == 401
    # Pas de challenge WWW-Authenticate : il déclenche la popup native du navigateur.
    assert "www-authenticate" not in response.headers


async def test_session_authenticated_returns_true(client, auth_headers):
    response = await client.get("/api/v1/session", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


async def test_health_stays_public_without_auth(client):
    """Le /health de déploiement doit rester accessible sans authentification."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
