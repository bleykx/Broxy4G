import base64
import os

import httpx
import pytest
import pytest_asyncio

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
