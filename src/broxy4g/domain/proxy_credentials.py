"""Identifiants 3proxy : génération sans caractères ambigus et hachage MD5-crypt.

Le mot de passe clair est transitoire : il ne doit jamais être persisté,
loggué ni exposé dans les réponses API. Seul `password_hash` (format 3proxy
`CR`) est stocké.
"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from passlib.hash import md5_crypt

# Alphabets sans caractères ambigus : pas de 0/o/O/l/1/I ni de caractère spécial.
USER_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
PASSWORD_ALPHABET = USER_ALPHABET + "ABCDEFGHJKLMNPQRSTUVWXYZ"

_T = TypeVar("_T")


class ChoiceRng(Protocol):
    """Objet minimal de génération aléatoire (secrets, random.Random, ...)."""

    def choice(self, seq: Sequence[_T]) -> _T: ...


@dataclass(frozen=True)
class ProxyCredentials:
    username: str
    password: str  # clair, transitoire — JAMAIS persisté ni loggué
    password_hash: str  # format 3proxy CR : "$1$<salt>$<hash>"


def generate_username(length: int = 12, *, rng: ChoiceRng | None = None) -> str:
    """Génère un nom d'utilisateur 3proxy de longueur `length`."""
    if length < 1:
        raise ValueError("length doit être >= 1")
    rng = secrets if rng is None else rng
    return "".join(rng.choice(USER_ALPHABET) for _ in range(length))


def generate_password(length: int = 32, *, rng: ChoiceRng | None = None) -> str:
    """Génère un mot de passe 3proxy de longueur `length`."""
    if length < 1:
        raise ValueError("length doit être >= 1")
    rng = secrets if rng is None else rng
    return "".join(rng.choice(PASSWORD_ALPHABET) for _ in range(length))


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Hache un mot de passe avec MD5-crypt (format 3proxy `CR`, préfixe `$1$`)."""
    if salt is None:
        return md5_crypt.hash(password)
    return md5_crypt.hash(password, salt=salt)


def generate_credentials(*, rng: ChoiceRng | None = None) -> ProxyCredentials:
    """Génère un jeu complet d'identifiants (login, mot de passe clair, hash)."""
    username = generate_username(rng=rng)
    password = generate_password(rng=rng)
    return ProxyCredentials(
        username=username,
        password=password,
        password_hash=hash_password(password),
    )
