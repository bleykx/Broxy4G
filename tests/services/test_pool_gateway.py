"""Tests du gateway de pool sur sockets loopback (aucun matériel).

Un faux proxy amont tient le rôle du membre 3proxy : il enregistre les têtes
reçues et répond en écho pour les tunnels. Le gateway est piloté par des stubs
de membership et d'écoute, donc aucun accès base ni Docker n'est nécessaire.
"""

import asyncio
import base64
import contextlib
import socket
from uuid import uuid4

import pytest

from broxy4g.domain.pool_listen import PoolRuntime
from broxy4g.domain.pool_selection import PoolCandidate
from broxy4g.services.pool_gateway import AsyncioPoolGateway

POOL_USER = "pooluser"
POOL_PASSWORD = "poolpass"
MEMBER_USER = "memberuser"
MEMBER_PASSWORD = "memberpass"


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubListen:
    """Service d'écoute dont le bind est imposé (ou absent si ``None``)."""

    def __init__(self, bind_ip="127.0.0.1"):
        self.bind_ip = bind_ip

    async def resolve_bind_ip(self):
        return self.bind_ip


class StubMembership:
    """Candidats éligibles imposés par le test."""

    def __init__(self):
        self.result: list[PoolCandidate] = []

    async def candidates(self, pool_id):
        return list(self.result)


class FakeUpstream:
    """Faux proxy amont : enregistre les têtes, tunnel en écho, 200 + ``ok``."""

    def __init__(self):
        self.requests: list[bytes] = []
        self.server = None
        self.port = 0

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            self.requests.append(head)
            if head.startswith(b"CONNECT"):
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                while data := await reader.read(1024):
                    writer.write(b"echo:" + data)
                    await writer.drain()
            else:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def close(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


def _runtime(port: int) -> PoolRuntime:
    return PoolRuntime(
        pool_id=uuid4(),
        label="pool",
        listen_port=port,
        username=POOL_USER,
        password=POOL_PASSWORD,
    )


def _candidate(port: int) -> PoolCandidate:
    return PoolCandidate(
        proxy_id=uuid4(),
        host="127.0.0.1",
        port=port,
        username=MEMBER_USER,
        password=MEMBER_PASSWORD,
    )


def _pool_auth() -> bytes:
    return f"Proxy-Authorization: Basic {_b64(f'{POOL_USER}:{POOL_PASSWORD}')}\r\n".encode()


async def _read_head(reader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)


async def _open(port: int):
    return await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5)


def _member_auth_encoded() -> str:
    return _b64(f"{MEMBER_USER}:{MEMBER_PASSWORD}")


async def _gateway(membership=None, listen=None):
    return AsyncioPoolGateway(membership or StubMembership(), listen or StubListen())


async def test_status_reports_listening():
    gateway = await _gateway()
    runtime = _runtime(_free_port())
    try:
        await gateway.sync([runtime])
        status = gateway.status(runtime.pool_id)
        assert status.listening is True
        assert status.bind_ip == "127.0.0.1"
        assert status.error is None
        await gateway.stop_all()
        assert gateway.status(runtime.pool_id).listening is False
    finally:
        await gateway.stop_all()


async def test_sync_twice_keeps_single_listener():
    gateway = await _gateway()
    runtime = _runtime(_free_port())
    try:
        await gateway.sync([runtime])
        await gateway.sync([runtime])
        assert gateway.status(runtime.pool_id).listening is True
        _reader, writer = await _open(runtime.listen_port)
        writer.close()
        await writer.wait_closed()
    finally:
        await gateway.stop_all()


async def test_sync_empty_stops_listener():
    gateway = await _gateway()
    runtime = _runtime(_free_port())
    try:
        await gateway.sync([runtime])
        await gateway.sync([])
        assert gateway.status(runtime.pool_id).listening is False
        with pytest.raises(OSError):
            _reader, writer = await _open(runtime.listen_port)
            writer.close()
    finally:
        await gateway.stop_all()


async def test_missing_bind_interface_reports_error():
    gateway = await _gateway(listen=StubListen(bind_ip=None))
    runtime = _runtime(_free_port())
    try:
        await gateway.sync([runtime])
        status = gateway.status(runtime.pool_id)
        assert status.listening is False
        assert status.error == "interface d'écoute absente"
    finally:
        await gateway.stop_all()


async def test_missing_credentials_gets_407():
    upstream = await FakeUpstream().start()
    membership = StubMembership()
    membership.result = [_candidate(upstream.port)]
    gateway = await _gateway(membership)
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        writer.write(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        head = await _read_head(reader)
        assert head.startswith(b"HTTP/1.1 407")
        assert b'Proxy-Authenticate: Basic realm="broxy4g-pool"' in head
        assert upstream.requests == []
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()
        await upstream.close()


async def test_wrong_pool_password_gets_407():
    upstream = await FakeUpstream().start()
    membership = StubMembership()
    membership.result = [_candidate(upstream.port)]
    gateway = await _gateway(membership)
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        wrong = _b64(f"{POOL_USER}:wrong")
        writer.write(
            f"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n"
            f"Proxy-Authorization: Basic {wrong}\r\n\r\n".encode()
        )
        await writer.drain()
        head = await _read_head(reader)
        assert head.startswith(b"HTTP/1.1 407")
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()
        await upstream.close()


async def test_no_eligible_member_gets_502():
    membership = StubMembership()
    gateway = await _gateway(membership)
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        writer.write(
            b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n"
            + _pool_auth()
            + b"\r\n"
        )
        await writer.drain()
        head = await _read_head(reader)
        assert head.startswith(b"HTTP/1.1 502")
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()


async def test_connect_tunnel_uses_client_target_and_member_credentials():
    upstream = await FakeUpstream().start()
    membership = StubMembership()
    membership.result = [_candidate(upstream.port)]
    gateway = await _gateway(membership)
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        writer.write(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n"
            + _pool_auth()
            + b"\r\n"
        )
        await writer.drain()
        head = await _read_head(reader)
        assert head.startswith(b"HTTP/1.1 200 Connection established")

        assert len(upstream.requests) == 1
        forwarded = upstream.requests[0]
        assert b"CONNECT example.com:443 HTTP/1.1" in forwarded
        assert f"Proxy-Authorization: Basic {_member_auth_encoded()}".encode() in forwarded
        assert _b64(f"{POOL_USER}:{POOL_PASSWORD}").encode() not in forwarded

        writer.write(b"ping")
        await writer.drain()
        echoed = await asyncio.wait_for(reader.read(64), timeout=5)
        assert echoed == b"echo:ping"
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()
        await upstream.close()


async def test_plain_http_uses_origin_form():
    upstream = await FakeUpstream().start()
    membership = StubMembership()
    membership.result = [_candidate(upstream.port)]
    gateway = await _gateway(membership)
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        writer.write(
            b"GET http://example.com/x?a=1 HTTP/1.1\r\nHost: example.com\r\n"
            b"Proxy-Connection: keep-alive\r\n" + _pool_auth() + b"\r\n"
        )
        await writer.drain()
        payload = await asyncio.wait_for(reader.read(), timeout=5)
        assert payload.endswith(b"ok")

        assert len(upstream.requests) == 1
        forwarded = upstream.requests[0]
        assert forwarded.startswith(b"GET /x?a=1 HTTP/1.1")
        assert b"Connection: close" in forwarded
        assert f"Proxy-Authorization: Basic {_member_auth_encoded()}".encode() in forwarded
        assert _b64(f"{POOL_USER}:{POOL_PASSWORD}").encode() not in forwarded
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()
        await upstream.close()


async def test_unreachable_member_gets_502():
    membership = StubMembership()
    membership.result = [_candidate(_free_port())]
    gateway = await _gateway(membership)
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        writer.write(
            b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n"
            + _pool_auth()
            + b"\r\n"
        )
        await writer.drain()
        head = await _read_head(reader)
        assert head.startswith(b"HTTP/1.1 502")
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()


async def test_malformed_request_gets_400():
    gateway = await _gateway()
    runtime = _runtime(_free_port())
    writer = None
    try:
        await gateway.sync([runtime])
        reader, writer = await _open(runtime.listen_port)
        writer.write(b"garbage\r\n\r\n")
        await writer.drain()
        head = await _read_head(reader)
        assert head.startswith(b"HTTP/1.1 400")
    finally:
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        await gateway.stop_all()
