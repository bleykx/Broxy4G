"""Tests d'éligibilité des membres de pool (règles métier, une raison à la fois)."""

import itertools
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from broxy4g.domain.dongle import Dongle
from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState
from broxy4g.domain.proxy_config import INTERNAL_PROXY_PORT
from broxy4g.domain.proxy_pool import ProxyPool
from broxy4g.repositories.database import ProxyPoolMemberORM
from broxy4g.services.pool_membership import PoolMembershipService

_PORTS = itertools.count(31201)
_POOL_PORTS = itertools.count(31350)
_UNSET = object()  # marqueur : chiffrer le mot de passe par défaut


def _make_service(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock) -> PoolMembershipService:
    return PoolMembershipService(pool_repo, proxy_repo, dongle_repo, secret_box, reset_lock=fake_reset_lock)


async def _member(
    deps,
    *,
    pool: ProxyPool | None = None,
    dongle_state=DongleState.ONLINE,
    proxy_state=ProxyState.RUNNING,
    password="secret",
    network_ip="172.28.0.5",
    enabled=True,
    source=ProxySource.DONGLE,
    with_dongle=True,
    lock=False,
    password_encrypted=_UNSET,
) -> tuple[UUID, UUID]:
    """Crée dongle + proxy persistés, le couple membre d'un pool ; pose le verrou si demandé.

    ``pool`` : pool existant auquel rattacher le membre ; sinon un pool neuf.
    Retourne ``(pool_id, proxy_id)``.
    """
    pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock = deps
    dongle_id = None
    if with_dongle:
        dongle = Dongle(
            identity=IdentityMaterial(imei=f"35{uuid4().hex[:14]}"),
            label="test",
            state=dongle_state,
        )
        await dongle_repo.save(dongle)
        dongle_id = dongle.id
    if password_encrypted is _UNSET:
        encrypted = secret_box.encrypt(password)
    else:
        encrypted = password_encrypted
    proxy = Proxy(
        dongle_id=dongle_id,
        container_name=f"px-{uuid4().hex[:8]}",
        listen_port=next(_PORTS),
        state=proxy_state,
        auth_user="alice",
        password_encrypted=encrypted,
        source_type=source,
        enabled=enabled,
        network_ip=network_ip,
    )
    await proxy_repo.save(proxy)
    if pool is None:
        pool = await pool_repo.save(ProxyPool(label=f"p{next(_POOL_PORTS)}", listen_port=next(_POOL_PORTS)))
    await pool_repo.add_member(pool.id, proxy.id)
    if lock:
        assert dongle_id is not None
        fake_reset_lock.acquire(str(dongle.id))
    return pool.id, proxy.id


def _order_members(session_factory, pool_id: UUID, proxy_ids: list[UUID]) -> None:
    """Force ``created_at`` croissant selon l'ordre d'ajout (déterminisme des tests)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with session_factory() as session:
        for index, proxy_id in enumerate(proxy_ids):
            session.query(ProxyPoolMemberORM).filter_by(pool_id=pool_id, proxy_id=proxy_id).update(
                {"created_at": base + timedelta(seconds=index)}
            )
        session.commit()


async def test_proxy_absent(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    svc = _make_service(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    pool = await pool_repo.save(ProxyPool(label="p", listen_port=31301))
    await pool_repo.add_member(pool.id, uuid4())  # proxy qui n'existe pas
    [evaluation] = await svc.evaluate(pool.id)
    assert evaluation.eligible is False
    assert evaluation.reason == "proxy absent"


async def test_proxy_disabled(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, proxy_id = await _member(deps, enabled=False)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.eligible is False
    assert evaluation.reason == "proxy désactivé"
    assert evaluation.proxy_id == proxy_id


async def test_proxy_not_running(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, proxy_state=ProxyState.STOPPED)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "proxy non démarré (state=STOPPED)"


async def test_internal_address_unknown(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, network_ip=None)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "adresse interne inconnue"


async def test_credentials_unrecoverable(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, password_encrypted=None)  # jamais chiffré : non récupérable
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "identifiants non récupérables"


async def test_dongle_absent(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, source=ProxySource.DONGLE, with_dongle=False)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "dongle absent"


async def test_dongle_resetting_state_excluded(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, dongle_state=DongleState.RESETTING)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "dongle en reset"


async def test_dongle_online_but_reset_lock_held_excluded(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    """Un ONLINE avec le verrou de reset tenu est exclu (reset en cours)."""
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, dongle_state=DongleState.ONLINE, lock=True)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "dongle en reset"


async def test_dongle_offline_excluded(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, dongle_state=DongleState.OFFLINE)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.reason == "dongle non connecté (state=OFFLINE)"


async def test_dongle_online_eligible_candidate(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, proxy_id = await _member(deps)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.eligible is True
    assert evaluation.reason is None
    assert evaluation.proxy_id == proxy_id
    assert evaluation.candidate is not None
    assert evaluation.candidate.host == "172.28.0.5"
    assert evaluation.candidate.port == INTERNAL_PROXY_PORT
    assert evaluation.candidate.username == "alice"
    assert evaluation.candidate.password == "secret"


async def test_dongle_degraded_eligible(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, dongle_state=DongleState.DEGRADED)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.eligible is True


async def test_local_proxy_without_dongle_eligible(pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, _ = await _member(deps, source=ProxySource.LOCAL, with_dongle=False)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.eligible is True
    assert evaluation.reason is None


async def test_evaluate_order_follows_member_ids(
    session_factory, pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock
):
    """L'ordre d'évaluation est l'ordre stable de list_member_ids (ordre d'ajout)."""
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool = await pool_repo.save(ProxyPool(label="p", listen_port=31301))
    proxy_ids = []
    for _ in range(3):
        _, proxy_id = await _member(deps, pool=pool)
        proxy_ids.append(proxy_id)
    _order_members(session_factory, pool.id, proxy_ids)
    evaluations = await svc.evaluate(pool.id)
    assert [e.proxy_id for e in evaluations] == proxy_ids


async def test_candidates_only_eligible_in_order(
    session_factory, pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock
):
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool = await pool_repo.save(ProxyPool(label="p", listen_port=31301))
    good_a = (await _member(deps, pool=pool))[1]
    bad = (await _member(deps, pool=pool, dongle_state=DongleState.OFFLINE))[1]
    good_b = (await _member(deps, pool=pool))[1]
    _order_members(session_factory, pool.id, [good_a, bad, good_b])
    candidates = await svc.candidates(pool.id)
    assert [c.proxy_id for c in candidates] == [good_a, good_b]


async def test_broken_member_excluded_without_exception(
    pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock, monkeypatch
):
    """Un membre dont la lecture échoue est exclu sans faire remonter l'exception."""
    deps = (pool_repo, proxy_repo, dongle_repo, secret_box, fake_reset_lock)
    svc = _make_service(*deps)
    pool_id, proxy_id = await _member(deps)

    async def _boom(pid):
        raise RuntimeError("base inaccessible")

    monkeypatch.setattr(proxy_repo, "get_by_id", _boom)
    [evaluation] = await svc.evaluate(pool_id)
    assert evaluation.eligible is False
    assert evaluation.reason == "membre illisible"
    assert evaluation.proxy_id == proxy_id
