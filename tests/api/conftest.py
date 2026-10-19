import base64

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from broxy4g.api.app import create_app
from broxy4g.repositories.database import create_engine, create_session_factory, init_db
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("BROXY4G_API_USER", "admin")
    monkeypatch.setenv("BROXY4G_API_PASSWORD", "test-pass")
    # Clé Fernet connue : permet la consultation des identifiants en test.
    monkeypatch.setenv("BROXY4G_PROXY_KEY", Fernet.generate_key().decode())
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    sf = create_session_factory(engine)
    return create_app(
        dongle_repo=DongleRepository(sf),
        proxy_repo=ProxyRepository(sf),
    )


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers():
    credentials = base64.b64encode(b"admin:test-pass").decode()
    return {"Authorization": f"Basic {credentials}"}
