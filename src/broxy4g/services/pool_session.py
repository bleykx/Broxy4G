"""Relais d'une connexion de pool vers un membre éligible (asyncio).

Séparé du gateway (qui possède l'écoute et l'état de sélection) : ici, une seule
connexion, un seul membre déjà choisi. Le client est authentifié avec les
identifiants du **pool**, puis la requête envoyée au membre porte les
identifiants **du membre** — les deux couples ne sont jamais confondus, et rien
n'est journalisé hormis ``pool_id``/``proxy_id``/raisons.
"""

import asyncio
import contextlib
import logging

from broxy4g.domain.pool_selection import PoolCandidate
from broxy4g.domain.proxy_auth import basic_authorization_header
from broxy4g.services.pool_http import (
    CONNECT_TIMEOUT_SECONDS,
    HEAD_TIMEOUT_SECONDS,
    MAX_HEAD_BYTES,
    PLAIN_HTTP_TIMEOUT_SECONDS,
    ClientRequest,
    bad_gateway_response,
    build_connect_request,
    build_forward_request,
    parse_status_code,
)
from broxy4g.services.pool_relay import splice

logger = logging.getLogger(__name__)


async def serve_candidate(
    pool_id,
    candidate: PoolCandidate,
    request: ClientRequest,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Ouvre la connexion membre puis sert la requête (tunnel ou relais en clair)."""
    try:
        backend_reader, backend_writer = await asyncio.wait_for(
            asyncio.open_connection(candidate.host, candidate.port, limit=MAX_HEAD_BYTES),
            CONNECT_TIMEOUT_SECONDS,
        )
    except (TimeoutError, OSError):
        logger.warning("Pool %s : membre %s injoignable", pool_id, candidate.proxy_id)
        writer.write(bad_gateway_response("membre injoignable"))
        await writer.drain()
        return

    try:
        if request.method == "CONNECT":
            await _serve_connect(pool_id, candidate, request, reader, writer, backend_reader, backend_writer)
        else:
            await _serve_plain(candidate, request, reader, writer, backend_reader, backend_writer)
    finally:
        with contextlib.suppress(Exception):
            backend_writer.close()
            await backend_writer.wait_closed()


async def _serve_connect(
    pool_id,
    candidate: PoolCandidate,
    request: ClientRequest,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    backend_reader: asyncio.StreamReader,
    backend_writer: asyncio.StreamWriter,
) -> None:
    """Établit le tunnel : ``200`` du membre obligatoire, puis splicing sans délai.

    Le ``CONNECT`` envoyé au membre porte la destination **demandée par le
    client** (``request.target``), jamais l'adresse du membre lui-même.
    """
    backend_writer.write(
        build_connect_request(
            request.target, basic_authorization_header(candidate.username, candidate.password)
        )
    )
    await backend_writer.drain()
    try:
        head = await asyncio.wait_for(backend_reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError, ConnectionError):
        writer.write(bad_gateway_response("membre indisponible"))
        await writer.drain()
        return
    if parse_status_code(head) != 200:
        logger.warning("Pool %s : membre %s refuse le tunnel", pool_id, candidate.proxy_id)
        writer.write(bad_gateway_response("membre indisponible"))
        await writer.drain()
        return
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    await splice(reader, writer, backend_reader, backend_writer)


async def _serve_plain(
    candidate: PoolCandidate,
    request: ClientRequest,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    backend_reader: asyncio.StreamReader,
    backend_writer: asyncio.StreamWriter,
) -> None:
    """Relaie une requête en clair ; timeout global borné, pas de keep-alive."""
    backend_writer.write(
        build_forward_request(request, basic_authorization_header(candidate.username, candidate.password))
    )
    await backend_writer.drain()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            splice(reader, writer, backend_reader, backend_writer),
            PLAIN_HTTP_TIMEOUT_SECONDS,
        )
