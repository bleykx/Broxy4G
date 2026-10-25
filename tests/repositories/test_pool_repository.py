"""Tests de persistance des pools et de leurs membres (SQLite en mémoire)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from broxy4g.domain.proxy_pool import ProxyPool
from broxy4g.repositories.pool_repository import PoolRepository


@pytest.fixture
def pool_repo(session_factory):
    return PoolRepository(session_factory)


def _pool(port: int = 31301, label: str = "prod", **kwargs) -> ProxyPool:
    defaults = dict(
        label=label,
        listen_port=port,
        auth_user="pooluser",
        password_encrypted="encrypted-token",
        enabled=True,
    )
    defaults.update(kwargs)
    return ProxyPool(**defaults)


async def test_save_get_round_trip(pool_repo):
    saved = await pool_repo.save(_pool())
    loaded = await pool_repo.get(saved.id)
    assert loaded is not None
    assert loaded.id == saved.id
    assert loaded.label == "prod"
    assert loaded.listen_port == 31301
    assert loaded.auth_user == "pooluser"
    assert loaded.password_encrypted == "encrypted-token"
    assert loaded.enabled is True


async def test_get_unknown_pool_is_none(pool_repo):
    assert await pool_repo.get(uuid4()) is None


async def test_save_twice_updates_same_row(pool_repo):
    pool = _pool()
    await pool_repo.save(pool)
    pool.label = "renommé"
    pool.enabled = False
    await pool_repo.save(pool)
    all_pools = await pool_repo.list_all()
    assert len(all_pools) == 1
    assert all_pools[0].label == "renommé"
    assert all_pools[0].enabled is False


async def test_list_all_sorted_by_listen_port(pool_repo):
    await pool_repo.save(_pool(port=31350, label="b"))
    await pool_repo.save(_pool(port=31310, label="a"))
    ports = [p.listen_port for p in await pool_repo.list_all()]
    assert ports == [31310, 31350]


async def test_used_ports(pool_repo):
    assert await pool_repo.used_ports() == set()
    await pool_repo.save(_pool(port=31301))
    await pool_repo.save(_pool(port=31320))
    assert await pool_repo.used_ports() == {31301, 31320}


async def test_add_member_idempotent(pool_repo):
    pool = await pool_repo.save(_pool())
    proxy_id = uuid4()
    assert await pool_repo.add_member(pool.id, proxy_id) is True
    assert await pool_repo.add_member(pool.id, proxy_id) is False
    assert await pool_repo.list_member_ids(pool.id) == [proxy_id]


async def test_remove_member(pool_repo):
    pool = await pool_repo.save(_pool())
    proxy_id = uuid4()
    await pool_repo.add_member(pool.id, proxy_id)
    assert await pool_repo.remove_member(pool.id, proxy_id) is True
    assert await pool_repo.remove_member(pool.id, proxy_id) is False


async def test_list_member_ids_sorted_by_created_at_then_proxy_id(pool_repo, session_factory):
    """Ordre stable : created_at (forcé) puis proxy_id (UUID hexadécimal)."""
    pool = await pool_repo.save(_pool())
    first, second = uuid4(), uuid4()
    first, second = sorted([first, second], key=str)
    from broxy4g.repositories.database import ProxyPoolMemberORM

    def member(proxy_id: UUID, created: datetime) -> ProxyPoolMemberORM:
        return ProxyPoolMemberORM(pool_id=pool.id, proxy_id=proxy_id, created_at=created)

    # created_at égal : l'ordre repose sur proxy_id (le plus petit en str d'abord).
    with session_factory() as session:
        session.add(member(first, datetime(2026, 1, 1, tzinfo=UTC)))
        session.add(member(second, datetime(2026, 1, 1, tzinfo=UTC)))
        session.commit()
    # created_at antérieur pour `second` : il passe devant malgré l'UUID plus grand.
    later = datetime(2026, 1, 2, tzinfo=UTC)
    with session_factory() as session:
        session.query(ProxyPoolMemberORM).filter_by(proxy_id=first).update({"created_at": later})
        session.commit()
    assert await pool_repo.list_member_ids(pool.id) == [second, first]


async def test_memberships_for_proxy(pool_repo):
    pool_a = await pool_repo.save(_pool(port=31301))
    pool_b = await pool_repo.save(_pool(port=31302))
    proxy_id = uuid4()
    await pool_repo.add_member(pool_a.id, proxy_id)
    await pool_repo.add_member(pool_b.id, proxy_id)
    assert set(await pool_repo.memberships_for_proxy(proxy_id)) == {pool_a.id, pool_b.id}
    assert await pool_repo.memberships_for_proxy(uuid4()) == []


async def test_delete_removes_members_and_pool(pool_repo):
    pool = await pool_repo.save(_pool())
    proxy_id = uuid4()
    await pool_repo.add_member(pool.id, proxy_id)
    assert await pool_repo.delete(pool.id) is True
    assert await pool_repo.delete(pool.id) is False
    assert await pool_repo.get(pool.id) is None
    assert await pool_repo.memberships_for_proxy(proxy_id) == []


async def test_prune_members_empty_set_removes_nothing(pool_repo):
    pool = await pool_repo.save(_pool())
    await pool_repo.add_member(pool.id, uuid4())
    assert await pool_repo.prune_members(set()) == 0
    assert len(await pool_repo.list_member_ids(pool.id)) == 1


async def test_prune_members_removes_orphans_only(pool_repo):
    pool = await pool_repo.save(_pool())
    valid, orphan = uuid4(), uuid4()
    await pool_repo.add_member(pool.id, valid)
    await pool_repo.add_member(pool.id, orphan)
    assert await pool_repo.prune_members({valid}) == 1
    assert await pool_repo.list_member_ids(pool.id) == [valid]
