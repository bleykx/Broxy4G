import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from broxy4g.repositories import init_db


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def dongle_repo(session_factory):
    from broxy4g.repositories import DongleRepository
    return DongleRepository(session_factory)


@pytest.fixture
def proxy_repo(session_factory):
    from broxy4g.repositories import ProxyRepository
    return ProxyRepository(session_factory)


@pytest.fixture
def allowed_host_repo(session_factory):
    from broxy4g.repositories import AllowedHostRepository
    return AllowedHostRepository(session_factory)


@pytest.fixture
def settings_repo(session_factory):
    from broxy4g.repositories import SettingsRepository
    return SettingsRepository(session_factory)