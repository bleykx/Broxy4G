"""Interlock de reset dans la réconciliation des proxies.

Un reset réellement en cours (dongle RESETTING ET verrou de reset tenu)
gère lui-même le conteneur proxy : la réconciliation ne doit ni le
reprovisionner ni le re-publier. Un RESETTING sans verrou (état résiduel
post-crash) est restauré normalement.
"""

from unittest.mock import AsyncMock, patch

from broxy4g.domain.dongle_states import DongleState
from broxy4g.domain.identity import IdentityMaterial
from broxy4g.domain.proxy import Proxy, ProxySource, ProxyState
from broxy4g.workers.proxy_reconciliation import ProxyReconciler


async def _enroll(enrollment_service):
    identity = IdentityMaterial(imei="123456789012345")
    return (await enrollment_service.enroll(identity, "Test", "test.apn")).dongle


async def _to_state(dongle, repo, target: DongleState):
    dongle.change_state(DongleState.CONNECTING)
    if target is not DongleState.ONLINE:
        dongle.change_state(DongleState.ONLINE)
    dongle.change_state(target)
    await repo.save(dongle)
    return await repo.get_by_id(dongle.id)


async def _seed_local_proxy(proxy_repo, *, interface="eth0"):
    proxy = Proxy(
        dongle_id=None,
        source_type=ProxySource.LOCAL,
        source_ref=interface,
        interface_name=interface,
        container_name="broxy4g-local",
        listen_port=31250,
    )
    proxy.network_ip = "172.28.0.150"
    proxy.route_table_id = 10002
    proxy.fwmark = 2
    proxy.auth_user = "user"
    proxy.password_hash = "CR:hash"
    await proxy_repo.save(proxy)
    return proxy


async def test_reconcile_proxies_skips_dongle_proxy_while_reset(
    reconciliation_worker, enrollment_service, proxy_repo,
    fake_reset_lock, fake_docker_compose, provisioning,
):
    """RESETTING + verrou tenu (chemin global) : reprovision ET ensure_publish absents."""
    dongle = await _to_state(await _enroll(enrollment_service), reconciliation_worker.dongle_repo, DongleState.RESETTING)
    fake_reset_lock.acquire(str(dongle.id))  # un reset tourne réellement
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    assert proxy is not None
    fake_docker_compose.set_state(proxy.container_name, "exited")  # le reset l'a arrêté

    with (
        patch.object(provisioning, "reprovision", new=AsyncMock()) as mock_repro,
        patch.object(provisioning, "ensure_publish", new=AsyncMock()) as mock_publish,
    ):
        await reconciliation_worker.reconcile_proxies()

    mock_repro.assert_not_awaited()
    mock_publish.assert_not_awaited()
    reloaded = await proxy_repo.get_by_id(proxy.id)
    assert reloaded.state is not ProxyState.CRASHED


async def test_restore_dongle_proxy_skipped_while_reset(
    reconciliation_worker, enrollment_service, proxy_repo,
    fake_reset_lock, fake_docker_compose, provisioning,
):
    """RESETTING + verrou tenu (chemin restore_dongle_proxy) : aucune action."""
    dongle = await _to_state(await _enroll(enrollment_service), reconciliation_worker.dongle_repo, DongleState.RESETTING)
    fake_reset_lock.acquire(str(dongle.id))
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    fake_docker_compose.set_state(proxy.container_name, "exited")

    with patch.object(provisioning, "reprovision", new=AsyncMock()) as mock_repro:
        await reconciliation_worker.proxy_reconciler.restore_dongle_proxy(dongle)

    mock_repro.assert_not_awaited()


async def test_reconcile_proxies_resetting_without_lock_reprovisions(
    reconciliation_worker, enrollment_service, proxy_repo,
    fake_docker_compose, provisioning,
):
    """RESETTING SANS verrou = état résiduel post-crash → le proxy est restauré."""
    dongle = await _to_state(await _enroll(enrollment_service), reconciliation_worker.dongle_repo, DongleState.RESETTING)
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    fake_docker_compose.set_state(proxy.container_name, "exited")

    with patch.object(provisioning, "reprovision", new=AsyncMock()) as mock_repro:
        await reconciliation_worker.reconcile_proxies()

    mock_repro.assert_awaited_once_with(proxy.id)


async def test_reconcile_proxies_online_missing_container_still_reprovisions(
    reconciliation_worker, enrollment_service, proxy_repo,
    fake_docker_compose, provisioning,
):
    """Non-régression : un dongle ONLINE dont le conteneur est absent est reprovisionné."""
    dongle = await _to_state(await _enroll(enrollment_service), reconciliation_worker.dongle_repo, DongleState.ONLINE)
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    fake_docker_compose.set_state(proxy.container_name, "exited")

    with patch.object(provisioning, "reprovision", new=AsyncMock()) as mock_repro:
        await reconciliation_worker.reconcile_proxies()

    mock_repro.assert_awaited_once_with(proxy.id)


async def test_reconcile_proxies_local_proxy_not_skipped_while_reset(
    reconciliation_worker, enrollment_service, proxy_repo,
    fake_reset_lock, fake_docker_compose, provisioning,
):
    """Un proxy de source LOCAL n'est pas concerné par le reset d'un dongle."""
    dongle = await _to_state(await _enroll(enrollment_service), reconciliation_worker.dongle_repo, DongleState.RESETTING)
    fake_reset_lock.acquire(str(dongle.id))
    dongle_proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    local_proxy = await _seed_local_proxy(proxy_repo)
    fake_docker_compose.set_state(dongle_proxy.container_name, "exited")
    fake_docker_compose.set_state(local_proxy.container_name, "exited")

    with patch.object(provisioning, "reprovision", new=AsyncMock()) as mock_repro:
        await reconciliation_worker.reconcile_proxies()

    mock_repro.assert_awaited_once_with(local_proxy.id)
    awaited_ids = [c.args[0] for c in mock_repro.await_args_list]
    assert dongle_proxy.id not in awaited_ids


async def test_reconcile_proxy_running_container_still_ensure_publish(
    reconciliation_worker, enrollment_service, proxy_repo, provisioning,
):
    """Conteneur déjà running (hors reset) : ensure_publish reste appelé."""
    dongle = await _to_state(await _enroll(enrollment_service), reconciliation_worker.dongle_repo, DongleState.ONLINE)
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)  # conteneur "running" (enrôlé)

    with patch.object(provisioning, "ensure_publish", new=AsyncMock()) as mock_publish:
        await reconciliation_worker.proxy_reconciler.reconcile_proxy(proxy)

    mock_publish.assert_awaited_once_with(proxy)


async def test_reconciler_without_reset_lock_keeps_original_behavior(
    dongle_repo, proxy_repo, enrollment_service,
    fake_docker_compose, provisioning,
):
    """reset_lock non fourni (None) : comportement d'origine, le proxy est restauré."""
    dongle = await _to_state(await _enroll(enrollment_service), dongle_repo, DongleState.RESETTING)
    proxy = await proxy_repo.get_by_dongle_id(dongle.id)
    fake_docker_compose.set_state(proxy.container_name, "exited")
    reconciler = ProxyReconciler(
        proxy_repo=proxy_repo,
        dongle_repo=dongle_repo,
        provisioning=provisioning,
        docker_compose=fake_docker_compose,
        reset_lock=None,
    )

    with patch.object(provisioning, "reprovision", new=AsyncMock()) as mock_repro:
        await reconciler.reconcile_proxy(proxy)

    mock_repro.assert_awaited_once_with(proxy.id)
