"""Gateway de pool : écoute HTTP proxy unique par pool, relais vers un membre.

Implémentation asyncio du contrat :class:`PoolGatewayPort` : un pool sans
membre éligible refuse (502), jamais de repli improvisé ; jamais
d'identifiant dans les journaux (seulement pool_id, proxy_id, ports, raisons).
"""

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import Sequence
from uuid import UUID

from broxy4g.domain.pool_listen import PoolListenStatus, PoolRuntime
from broxy4g.domain.pool_selection import pick_candidate
from broxy4g.domain.proxy_auth import (
    UNAUTHORIZED_RESPONSE,
    check_basic_authorization,
)
from broxy4g.services.pool_http import (
    HEAD_TIMEOUT_SECONDS,
    MAX_HEAD_BYTES,
    bad_gateway_response,
    bad_request_response,
    parse_request_head,
)
from broxy4g.services.pool_membership import PoolMembershipService
from broxy4g.services.pool_session import serve_candidate
from broxy4g.services.proxy_listen import ProxyListenService

logger = logging.getLogger(__name__)


class AsyncioPoolGateway:
    """Écoute les pools activés et relaie leurs connexions vers un membre."""

    def __init__(self, membership: PoolMembershipService, listen: ProxyListenService) -> None:
        self._membership = membership
        self._listen = listen
        self._servers: dict[UUID, asyncio.AbstractServer] = {}
        self._runtimes: dict[UUID, PoolRuntime] = {}
        self._bind_ip: str | None = None
        self._errors: dict[UUID, str] = {}
        self._in_flight: dict[UUID, dict[UUID, int]] = {}
        self._last_used: dict[UUID, dict[UUID, float]] = {}
        self._sync_lock = asyncio.Lock()

    async def sync(self, runtimes: Sequence[PoolRuntime]) -> None:
        """Réaligne l'écoute sur les pools voulus (absorbe toute exception)."""
        async with self._sync_lock:
            try:
                await self._sync(runtimes)
            except Exception:
                logger.exception("Synchronisation des écoutes de pool impossible")

    async def _sync(self, runtimes: Sequence[PoolRuntime]) -> None:
        desired = {r.pool_id: r for r in runtimes}
        bind_ip = await self._listen.resolve_bind_ip()
        if bind_ip is None:
            await self._close_all()
            self._reset_state()
            self._bind_ip = None
            for pool_id in desired:
                self._errors[pool_id] = "interface d'écoute absente"
            return

        changed = bind_ip != self._bind_ip
        if changed:
            await self._close_all()
            self._reset_state()
            self._bind_ip = bind_ip

        for pool_id in [pid for pid in self._servers if pid not in desired]:
            await self._close(pool_id)

        self._runtimes = desired

        for pool_id, runtime in desired.items():
            if pool_id in self._servers:
                continue
            try:
                server = await asyncio.start_server(
                    functools.partial(self._handle_client, pool_id=pool_id),
                    host=bind_ip,
                    port=runtime.listen_port,
                    limit=MAX_HEAD_BYTES,
                )
            except OSError:
                self._errors[pool_id] = f"écoute impossible sur le port {runtime.listen_port}"
                logger.warning("Pool %s : écoute impossible sur le port %s", pool_id, runtime.listen_port)
                continue
            self._servers[pool_id] = server
            self._errors.pop(pool_id, None)
            logger.info("Pool %s à l'écoute sur %s:%s", pool_id, bind_ip, runtime.listen_port)

    def status(self, pool_id: UUID) -> PoolListenStatus:
        """Statut d'écoute (jamais d'identifiant) : serveur, bind, erreur."""
        return PoolListenStatus(listening=pool_id in self._servers, bind_ip=self._bind_ip, error=self._errors.get(pool_id))

    async def stop_all(self) -> None:
        """Arrête toutes les écoutes de pool et purge l'état."""
        async with self._sync_lock:
            await self._close_all()
            self._reset_state()
            self._bind_ip = None

    def _reset_state(self) -> None:
        """Vide l'état dynamique (runtimes, erreurs, compteurs) sans toucher au bind."""
        self._runtimes, self._errors = {}, {}
        self._in_flight, self._last_used = {}, {}

    async def _close(self, pool_id: UUID) -> None:
        """Ferme le serveur d'un pool et retire son état (exceptions absorbées)."""
        server = self._servers.pop(pool_id, None)
        for state in (self._errors, self._in_flight, self._last_used):
            state.pop(pool_id, None)
        if server is None:
            return
        with contextlib.suppress(Exception):
            server.close()
            await server.wait_closed()

    async def _close_all(self) -> None:
        """Ferme tous les serveurs d'écoute (exceptions absorbées par _close)."""
        for pool_id in list(self._servers):
            await self._close(pool_id)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, pool_id) -> None:
        """Sert une connexion client ; toute exception est absorbée, le writer toujours fermé."""
        try:
            runtime = self._runtimes.get(pool_id)
            if runtime is None:
                return
            request = await self._read_request(reader, writer)
            if request is None:
                return
            if not check_basic_authorization(
                request.proxy_authorization, username=runtime.username, password=runtime.password
            ):
                writer.write(UNAUTHORIZED_RESPONSE)
                await writer.drain()
                return
            await self._relay(pool_id, runtime, request, reader, writer)
        except Exception:
            logger.exception("Pool %s : traitement de la connexion impossible", pool_id)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _read_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Lit et parse la tête client ; ``None`` si absente/illisible, 400 si ininterprétable."""
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError, ConnectionError):
            return None
        request = parse_request_head(raw)
        if request is None:
            writer.write(bad_request_response("requête invalide"))
            await writer.drain()
            return None
        return request

    async def _relay(self, pool_id, runtime, request, reader, writer) -> None:
        """Sélectionne un membre éligible et relaie (tunnel ou HTTP en clair)."""
        candidates = await self._membership.candidates(pool_id)
        in_flight = self._in_flight.get(pool_id)
        last_used = self._last_used.get(pool_id)
        candidate = pick_candidate(candidates, in_flight=in_flight, last_used=last_used)
        if candidate is None:
            writer.write(bad_gateway_response("aucun membre éligible"))
            await writer.drain()
            return

        pool_in_flight = self._in_flight.setdefault(pool_id, {})
        pool_last_used = self._last_used.setdefault(pool_id, {})
        pool_in_flight[candidate.proxy_id] = pool_in_flight.get(candidate.proxy_id, 0) + 1
        try:
            await serve_candidate(pool_id, candidate, request, reader, writer)
        finally:
            count = pool_in_flight.get(candidate.proxy_id, 1) - 1
            if count <= 0:
                pool_in_flight.pop(candidate.proxy_id, None)
            else:
                pool_in_flight[candidate.proxy_id] = count
            pool_last_used[candidate.proxy_id] = time.monotonic()

