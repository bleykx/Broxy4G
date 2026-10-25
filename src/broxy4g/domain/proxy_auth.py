"""Authentification HTTP Basic du port de pool (fonctions pures).

Le schéma ``Basic`` est insensible à la casse. Aucune fonction de ce module ne
journalise de mot de passe : les valeurs claires transitent uniquement en
mémoire pour la comparaison ou la construction d'en-têtes.
"""

import base64
import binascii
import hmac

REALM = "broxy4g-pool"

# Réponse 407 complète (HTTP/1.1), sans corps, pour un refus d'authentification.
# Elle ne distingue pas « utilisateur inconnu » de « mot de passe faux ».
UNAUTHORIZED_RESPONSE: bytes = (
    "HTTP/1.1 407 Proxy Authentication Required\r\n"
    f'Proxy-Authenticate: Basic realm="{REALM}"\r\n'
    "Content-Length: 0\r\n"
    "Connection: close\r\n"
    "\r\n"
).encode("ascii")


def parse_basic_authorization(header: str | None) -> tuple[str, str] | None:
    """Décoder un en-tête ``Proxy-Authorization: Basic <base64>``.

    Retourne ``(username, password)`` ou ``None`` si l'en-tête est absent, ne
    porte pas le schéma ``Basic``, que la base64 ou l'UTF-8 est invalide. Le
    mot de passe peut contenir ``:`` : on découpe sur le premier ``:`` seulement.
    """
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        payload = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    username, separator, password = payload.partition(":")
    if not separator:
        return None
    return username, password


def check_basic_authorization(
    header: str | None, *, username: str, password: str
) -> bool:
    """Vérifier l'en-tête de Basic auth contre les identifiants attendus.

    La comparaison est à temps constant (``hmac.compare_digest``) sur les deux
    champs. Retourne ``False`` si l'un des identifiants attendus est vide (un
    pool activé ne doit jamais exister sans identifiants) ou si l'en-tête est
    absent/invalide.
    """
    if not username or not password:
        return False
    parsed = parse_basic_authorization(header)
    if parsed is None:
        return False
    got_user, got_pass = parsed
    return (
        hmac.compare_digest(got_user.encode("utf-8"), username.encode("utf-8"))
        and hmac.compare_digest(got_pass.encode("utf-8"), password.encode("utf-8"))
    )


def basic_authorization_header(username: str, password: str) -> str:
    """Construire l'en-tête ``Basic`` pour les appels sortants du gateway."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"
