"""Construction du stack de workers et boucles de supervision.

Les workers utilisent leurs propres adaptateurs réels (aucun partage
d'instance avec l'API), y compris le chemin ADB issu de BROXY4G_ADB_PATH.
"""
import asyncio
import logging

from broxy4g.adapters.adb_real import RealAdb
from broxy4g.adapters.connectivity_real import RealConnectivity
from broxy4g.adapters.docker_network import RealDockerNetwork
from broxy4g.adapters.modem_manager_real import RealModemManager
from broxy4g.adapters.net_interface import RealInterface
from broxy4g.adapters.network_manager import RealNetworkManager
from broxy4g.adapters.nftables_real import RealNftables
from broxy4g.adapters.routing_real import RealRouting
from broxy4g.adapters.shell_real import RealShell
from broxy4g.adapters.usb_device import RealUsbDevice
from broxy4g.env_config import resolve_adb_path, resolve_usb_recovery_cooldown
from broxy4g.services.proxy_network import ProxyNetworkService
from broxy4g.services.routing_slot_migration import RoutingSlotMigration
from broxy4g.services.smartphone_uplink import SmartphoneUplinkService
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.services.usb_recovery import UsbRecoveryService
from broxy4g.workers.discovery import DiscoveryWorker
from broxy4g.workers.reconciliation import ReconciliationWorker

logger = logging.getLogger(__name__)


async def run_workers(app, stop_event: asyncio.Event, work_dir: str):
    """Run discovery and reconciliation workers until stopped."""
    shell = RealShell()

    # Create real adapters for workers
    modem = RealModemManager(shell)
    routing = RealRouting(shell)
    nft = RealNftables(shell, work_dir=work_dir)
    connectivity = RealConnectivity(shell)
    proxy_network = ProxyNetworkService(RealDockerNetwork(shell), RealInterface(shell))

    # Stack smartphone worker : mêmes adaptateurs réels et même chemin ADB
    # qu'au niveau API (BROXY4G_ADB_PATH), sans partage d'instance avec l'API.
    adb = RealAdb(shell, adb_path=resolve_adb_path())
    nm = RealNetworkManager(shell)
    uplink_iface = RealInterface(shell)
    worker_smartphone_uplink = SmartphoneUplinkService(
        adb=adb, nm=nm, iface=uplink_iface, routing=routing
    )
    discovery = DiscoveryWorker(
        modem, app.state.dongle_repo, apn_source=app.state.apn_source, adb=adb
    )
    worker_uplink_routes = UplinkRouteService(
        modem, routing, smartphone=worker_smartphone_uplink
    )
    usb_recovery = UsbRecoveryService(
        app.state.dongle_repo,
        worker_uplink_routes,
        RealUsbDevice(),
        reset_lock=app.state.reset_lock,
        cooldown_seconds=resolve_usb_recovery_cooldown(),
    )
    reconciliation = ReconciliationWorker(
        dongle_repo=app.state.dongle_repo,
        proxy_repo=app.state.proxy_repo,
        modem=modem,
        routing=routing,
        nft=nft,
        connectivity=connectivity,
        connection_service=app.state.connection_service,
        provisioning=app.state.provisioning,
        docker_compose=app.state.docker_compose,
        metrics_snapshot=app.state.metrics_snapshot,
        uplink_routes=worker_uplink_routes,
        slot_migration=RoutingSlotMigration(app.state.dongle_repo, app.state.proxy_repo),
        isolation=app.state.isolation,
        reset_lock=app.state.reset_lock,
        local_connections=app.state.local_connections_service,
        usb_recovery=usb_recovery,
        proxy_network=proxy_network,
        adb=adb,
        smartphone=worker_smartphone_uplink,
    )

    async def discovery_loop():
        while not stop_event.is_set():
            try:
                await discovery.discover()
            except Exception:
                logger.exception("Discovery error")
            await asyncio.sleep(10)

    async def reconciliation_loop():
        while not stop_event.is_set():
            try:
                await reconciliation.reconcile_all()
            except Exception:
                logger.exception("Reconciliation error")
            await asyncio.sleep(30)

    tasks = [asyncio.create_task(discovery_loop()), asyncio.create_task(reconciliation_loop())]
    await stop_event.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
