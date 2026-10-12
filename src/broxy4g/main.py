import argparse
import asyncio
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from broxy4g.adapters.connectivity_real import RealConnectivity
from broxy4g.adapters.docker_compose import RealDockerCompose
from broxy4g.adapters.local_uplink import RealLocalUplink
from broxy4g.adapters.modem_manager_real import RealModemManager
from broxy4g.adapters.net_interface import RealInterface
from broxy4g.adapters.nftables_real import RealNftables
from broxy4g.adapters.routing_real import RealRouting
from broxy4g.adapters.shell_real import RealShell
from broxy4g.api.app import create_app
from broxy4g.domain.reset_states import ResetLock
from broxy4g.repositories.database import create_engine, create_session_factory, init_db
from broxy4g.repositories.dongle_repository import DongleRepository
from broxy4g.repositories.proxy_repository import ProxyRepository
from broxy4g.services.uplink_routes import UplinkRouteService
from broxy4g.workers.discovery import DiscoveryWorker
from broxy4g.workers.reconciliation import ReconciliationWorker

logger = logging.getLogger(__name__)


def resolve_frontend_dist() -> Path:
    """Return the frontend build directory.

    Uses BROXY4G_FRONTEND_DIST env var if set and non-empty,
    otherwise falls back to the repo-relative path.
    """
    env_path = os.environ.get("BROXY4G_FRONTEND_DIST", "")
    if env_path:
        return Path(env_path)
    return Path(__file__).parent.parent.parent / "frontend" / "dist"


def register_frontend(app: FastAPI, frontend_dist: Path) -> None:
    """Serve the SPA for non-API routes (no-op if dist is absent).

    Unmatched ``/api/...`` paths return a 404 JSON error instead of the
    SPA ``index.html``, so a wrong API call is never silently "successful".
    """
    if not frontend_dist.exists():
        return

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = frontend_dist / path
        if path and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_dist / "index.html"))

    logger.info("Frontend served from %s", frontend_dist)


def parse_args():
    parser = argparse.ArgumentParser(description="Broxy4G - 4G/5G Dongle Manager")
    parser.add_argument("--host", default=os.environ.get("BROXY4G_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BROXY4G_PORT", "8080")))
    parser.add_argument("--db", default=os.environ.get("BROXY4G_DB_PATH", "/var/lib/broxy4g/broxy4g.db"))
    return parser.parse_args()


async def run_workers(app, stop_event: asyncio.Event, work_dir: str):
    """Run discovery and reconciliation workers until stopped."""
    shell = RealShell()
    
    # Create real adapters for workers
    modem = RealModemManager(shell)
    routing = RealRouting(shell)
    nft = RealNftables(shell, work_dir=work_dir)
    connectivity = RealConnectivity(shell)
    
    discovery = DiscoveryWorker(modem, app.state.dongle_repo, apn_source=app.state.apn_source)
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
        uplink_routes=UplinkRouteService(modem, routing),
        isolation=app.state.isolation,
        reset_lock=app.state.reset_lock,
        local_connections=app.state.local_connections_service,
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


async def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Répertoire d'état inscriptible (jamais le temp dir système, qui est en
    # lecture seule sous `ProtectSystem=strict`). Sert aussi de work dir nft.
    work_dir = str(db_path.parent)

    engine = create_engine(f"sqlite:///{db_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)

    dongle_repo = DongleRepository(session_factory)
    proxy_repo = ProxyRepository(session_factory)

    # Create the app with real adapters
    app = create_app(
        dongle_repo=dongle_repo,
        proxy_repo=proxy_repo,
        modem=RealModemManager(RealShell()),
        iface=RealInterface(RealShell()),
        routing=RealRouting(RealShell()),
        nft=RealNftables(RealShell(), work_dir=work_dir),
        connectivity=RealConnectivity(RealShell()),
        docker_compose=RealDockerCompose(RealShell()),
        local_uplink=RealLocalUplink(RealShell()),
        reset_lock=ResetLock(),
        data_dir=os.environ.get("BROXY4G_DATA_DIR", str(db_path.parent / "proxies")),
    )

    # Serve frontend for non-API routes
    register_frontend(app, resolve_frontend_dist())

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_workers(app, stop_event, work_dir))

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        await server_task
    finally:
        stop_event.set()
        await worker_task


if __name__ == "__main__":
    asyncio.run(main())