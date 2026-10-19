from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from broxy4g.domain.proxy import Proxy, ProxySource


@pytest.mark.asyncio
async def test_proxy_repository_save_and_get_by_id(proxy_repo, session_factory):
    # Création d'un proxy
    proxy = Proxy(dongle_id=uuid4(), container_name="test-proxy", listen_port=8080)
    saved = await proxy_repo.save(proxy)
    assert saved.id == proxy.id

    # Récupération par ID
    retrieved = await proxy_repo.get_by_id(proxy.id)
    assert retrieved is not None
    assert retrieved.id == proxy.id


@pytest.mark.asyncio
async def test_proxy_repository_save_and_get_by_dongle_id(proxy_repo, session_factory):
    # Création d'un proxy
    dongle_id = uuid4()
    proxy = Proxy(dongle_id=dongle_id, container_name="test-proxy", listen_port=8080)
    await proxy_repo.save(proxy)

    # Récupération par dongle_id
    retrieved = await proxy_repo.get_by_dongle_id(dongle_id)
    assert retrieved is not None
    assert retrieved.id == proxy.id


@pytest.mark.asyncio
async def test_proxy_repository_list_by_dongle(proxy_repo, session_factory):
    # Création de deux proxies pour le même dongle
    dongle_id = uuid4()
    proxy1 = Proxy(dongle_id=dongle_id, container_name="test-proxy-1", listen_port=8080)
    proxy2 = Proxy(dongle_id=dongle_id, container_name="test-proxy-2", listen_port=8081)
    await proxy_repo.save(proxy1)
    await proxy_repo.save(proxy2)

    # Liste par dongle
    proxies = await proxy_repo.list_by_dongle(dongle_id)
    assert len(proxies) == 2
    ids = [p.id for p in proxies]
    assert proxy1.id in ids
    assert proxy2.id in ids


@pytest.mark.asyncio
async def test_proxy_repository_delete(proxy_repo, session_factory):
    # Création d'un proxy
    proxy = Proxy(dongle_id=uuid4(), container_name="test-proxy", listen_port=8080)
    saved = await proxy_repo.save(proxy)

    # Suppression
    deleted = await proxy_repo.delete(saved.id)
    assert deleted is True

    # Vérification de la suppression
    retrieved = await proxy_repo.get_by_id(saved.id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_proxy_repository_get_by_dongle_id_none(proxy_repo, session_factory):
    # Récupération d'un proxy inexistant pour un dongle
    retrieved = await proxy_repo.get_by_dongle_id(uuid4())
    assert retrieved is None


@pytest.mark.asyncio
async def test_proxy_repository_new_fields_persisted(proxy_repo):
    proxy = Proxy(
        dongle_id=None,
        container_name="local-proxy",
        listen_port=31201,
        source_type=ProxySource.LOCAL,
        source_ref="local",
        interface_name="eth0",
        enabled=False,
        config_path="/etc/broxy4g/local.cfg",
        compose_path="/opt/broxy4g/local.yml",
        network_ip="172.20.0.2",
    )
    saved = await proxy_repo.save(proxy)
    retrieved = await proxy_repo.get_by_id(saved.id)

    assert retrieved.source_type == ProxySource.LOCAL
    assert retrieved.source_ref == "local"
    assert retrieved.interface_name == "eth0"
    assert retrieved.enabled is False
    assert retrieved.config_path == "/etc/broxy4g/local.cfg"
    assert retrieved.compose_path == "/opt/broxy4g/local.yml"
    assert retrieved.network_ip == "172.20.0.2"


@pytest.mark.asyncio
async def test_proxy_repository_save_updates_new_fields(proxy_repo):
    proxy = Proxy(dongle_id=uuid4(), container_name="p", listen_port=31210)
    saved = await proxy_repo.save(proxy)
    assert saved.enabled is True
    assert saved.source_type == ProxySource.DONGLE

    saved.enabled = False
    saved.source_ref = "updated"
    await proxy_repo.save(saved)
    retrieved = await proxy_repo.get_by_id(saved.id)
    assert retrieved.enabled is False
    assert retrieved.source_ref == "updated"


@pytest.mark.asyncio
async def test_proxy_repository_get_by_source(proxy_repo):
    proxy = Proxy(
        dongle_id=None,
        container_name="p",
        listen_port=31220,
        source_type=ProxySource.LOCAL,
        source_ref="local",
    )
    await proxy_repo.save(proxy)

    retrieved = await proxy_repo.get_by_source(ProxySource.LOCAL, "local")
    assert retrieved is not None
    assert retrieved.id == proxy.id
    assert await proxy_repo.get_by_source(ProxySource.DONGLE, "local") is None


@pytest.mark.asyncio
async def test_proxy_repository_list_all(proxy_repo):
    await proxy_repo.save(Proxy(dongle_id=None, container_name="a", listen_port=31230))
    await proxy_repo.save(Proxy(dongle_id=uuid4(), container_name="b", listen_port=31231))

    all_proxies = await proxy_repo.list_all()
    assert len(all_proxies) == 2
    assert [p.listen_port for p in all_proxies] == [31230, 31231]


@pytest.mark.asyncio
async def test_proxy_repository_used_ports(proxy_repo):
    assert await proxy_repo.used_ports() == set()
    await proxy_repo.save(Proxy(dongle_id=None, container_name="a", listen_port=31240))
    await proxy_repo.save(Proxy(dongle_id=None, container_name="b", listen_port=31241))
    assert await proxy_repo.used_ports() == {31240, 31241}


@pytest.mark.asyncio
async def test_proxy_repository_persists_encrypted(proxy_repo):
    proxy = Proxy(
        dongle_id=uuid4(),
        container_name="p",
        listen_port=31260,
        auth_user="khmtpznwqrbv",
        password_hash="$1$salt$hash",
        password_encrypted="fernet-token-value",
    )
    saved = await proxy_repo.save(proxy)
    retrieved = await proxy_repo.get_by_id(saved.id)
    assert retrieved.password_encrypted == "fernet-token-value"

    # Mise à jour via _apply_to_orm (changement du secret chiffré).
    retrieved.password_encrypted = "new-fernet-token"
    await proxy_repo.save(retrieved)
    again = await proxy_repo.get_by_id(saved.id)
    assert again.password_encrypted == "new-fernet-token"


def test_migration_adds_password_encrypted_column(tmp_path):
    """Base préexistante sans la colonne → init_db l'ajoute (idempotent)."""
    from sqlalchemy import create_engine, inspect, text

    from broxy4g.repositories.database import init_db

    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE proxies ("
                "id VARCHAR PRIMARY KEY, dongle_id VARCHAR, container_name VARCHAR,"
                " listen_port INTEGER, state VARCHAR, auth_user VARCHAR,"
                " password_hash VARCHAR, source_type VARCHAR, source_ref VARCHAR"
                ")"
            )
        )
    init_db(engine)  # 1er appel : ajoute password_encrypted
    init_db(engine)  # 2e appel : idempotent
    cols = {c["name"] for c in inspect(engine).get_columns("proxies")}
    assert "password_encrypted" in cols


@pytest.mark.asyncio
async def test_proxy_repository_listen_port_unique(proxy_repo):
    await proxy_repo.save(Proxy(dongle_id=None, container_name="a", listen_port=31250))
    other = Proxy(dongle_id=None, container_name="b", listen_port=31250)
    with pytest.raises(IntegrityError):
        await proxy_repo.save(other)