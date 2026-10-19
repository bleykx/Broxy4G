import base64

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from broxy4g.adapters.modem_manager import BearerIpConfig, ModemInfo
from broxy4g.api.app import create_app
from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
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


@pytest_asyncio.fixture
async def enroll_online(app):
    """Retourne ``enroll(imei, interface, table_id, fwmark)`` : enrôle un dongle
    via l'app, le passe en ONLINE avec modem+bearer prêts pour un reset DONE."""

    async def _enroll(imei, interface, table_id, fwmark) -> Dongle:
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

    return _enroll
