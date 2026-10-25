"""Parsing/écriture HTTP pur du protocole proxy du port de pool (aucune I/O).

Fonctions pures sur des ``bytes`` : la requête client est découpée en
:class:`ClientRequest`, les requêtes sortantes vers le membre (``CONNECT`` ou
HTTP en clair absolu) et les réponses d'erreur sont assemblées ici. Toute
forme inattendue renvoie ``None`` (requête) : c'est l'appelant qui décide de
la réponse 400. Rien de ce module ne contient d'identifiant du pool ni du
membre hormis l'en-tête ``authorization`` transmis explicitement par
l'appelant ; rien n'est journalisé ici.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_HEAD_BYTES = 65536
HEAD_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 10.0
PLAIN_HTTP_TIMEOUT_SECONDS = 300.0

# En-têtes sautés quand on re-relaye la requête vers le membre (RFC 7230) :
# comparaison en minuscules. ``transfer-encoding`` est CONSERVÉ : le corps est
# relayé tel quel (un corps chunked resterait sinon illisible par le membre).
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authorization",
        "te",
        "trailer",
        "upgrade",
    }
)


@dataclass(frozen=True)
class ClientRequest:
    """Requête client normalisée pour le relais.

    ``target`` : ``host:port`` pour ``CONNECT``, chemin d'origine (``/p?q``)
    sinon. ``host``/``port`` : destination résolue (port 80 par défaut).
    """

    method: str
    target: str
    host: str
    port: int
    version: str
    headers: tuple[tuple[str, str], ...]
    proxy_authorization: str | None


def _parse_connect_target(target: str) -> tuple[str, int] | None:
    """``host:port`` : dernier ``:`` séparateur ; aucun ``:`` en plus (pas d'IPv6)."""
    if target.count(":") != 1:
        return None
    host, _, port = target.rpartition(":")
    if not host or not (port.isascii() and port.isdigit()):
        return None
    number = int(port)
    if not 1 <= number <= 65535:
        return None
    return host, number


def _parse_absolute_target(target: str) -> tuple[str, int, str] | None:
    """URL absolue ``http://…`` → ``(host, port, chemin d'origine)`` ; sinon ``None``."""
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        return None
    try:
        port = parts.port or 80
    except ValueError:  # port absent/invalidé par urlsplit
        return None
    path = parts.path or "/"  # path vide : "/" (avant un éventuel query)
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.hostname, port, path


def parse_request_head(raw: bytes) -> ClientRequest | None:
    """Décode la tête d'une requête client ; ``None`` si malformée."""
    lines = raw.decode("latin-1", errors="replace").split("\r\n")
    if lines and lines[0] == "":
        lines = lines[1:]
    if not lines:
        return None
    request_line = lines[0].split(" ", 2)
    if len(request_line) != 3:
        return None
    method, target, version = request_line
    if not method or not target or not version.startswith("HTTP/1."):
        return None

    headers: list[tuple[str, str]] = []
    proxy_authorization: str | None = None
    for line in lines[1:]:
        if line == "":
            continue  # la ligne vide finale de la séparation tête/corps
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            return None
        name, value = name.strip(), value.strip()
        if name.lower() == "proxy-authorization" and proxy_authorization is None:
            proxy_authorization = value
        headers.append((name, value))

    if method == "CONNECT":
        resolved = _parse_connect_target(target)
        if resolved is None:
            return None
        host, port = resolved
    else:
        resolved = _parse_absolute_target(target)
        if resolved is None:
            return None
        host, port, target = resolved
    return ClientRequest(
        method=method,
        target=target,
        host=host,
        port=port,
        version=version,
        headers=tuple(headers),
        proxy_authorization=proxy_authorization,
    )


def parse_status_code(head: bytes) -> int | None:
    """Code statut d'une ligne de réponse ``HTTP/1.x NNN …`` ; ``None`` sinon."""
    line = head.decode("latin-1", errors="replace").split("\r\n", 1)[0]
    parts = line.split(" ", 2)
    if len(parts) < 2 or not line.startswith("HTTP/1."):
        return None
    status = parts[1]
    if len(status) != 3 or not status.isdigit():
        return None
    return int(status)


def build_connect_request(target: str, authorization: str) -> bytes:
    """Requête ``CONNECT`` vers le membre (sans ``Connection: close`` : tunnel).

    ``target`` est la destination ``host:port`` **du client** (jamais l'adresse
    du membre) : le membre ouvre le tunnel vers la cible demandée.
    """
    return (
        f"CONNECT {target} HTTP/1.1\r\n"
        f"Host: {target}\r\n"
        f"Proxy-Authorization: {authorization}\r\n"
        "\r\n"
    ).encode("latin-1")


def build_forward_request(request: ClientRequest, authorization: str) -> bytes:
    """Requête en clair vers le membre : cible d'origine, en-têtes nettoyés.

    Les en-têtes de saut sont retirés ; le ``Host`` du client est conservé tel
    quel (jamais un second ``Host`` ajouté) ; ``Proxy-Authorization`` (celui du
    membre) et ``Connection: close`` sont imposés.
    """
    lines = [f"{request.method} {request.target} {request.version}"]
    for name, value in request.headers:
        if name.lower() in HOP_BY_HOP:
            continue
        lines.append(f"{name}: {value}")
    lines.append(f"Proxy-Authorization: {authorization}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def response(
    status: int,
    reason: str,
    *,
    body: bytes = b"",
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> bytes:
    """Réponse complète avec ``Content-Length`` et ``Connection: close``."""
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
    )
    for name, value in extra_headers:
        head += f"{name}: {value}\r\n"
    return (head + "\r\n").encode("latin-1") + body


def bad_request_response(reason: str) -> bytes:
    """Réponse ``400`` pour une requête client ininterprétable."""
    return response(400, reason)


def bad_gateway_response(reason: str) -> bytes:
    """Réponse ``502`` quand aucun membre éligible ne peut être joint."""
    return response(502, reason)
