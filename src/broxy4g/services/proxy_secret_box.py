"""Coffre de secret proxy : chiffrement réversible (Fernet) du mot de passe.

Encapsule le chiffrement/déchiffrement du mot de passe 3proxy au repos, pour
en permettre la consultation par un utilisateur authentifié. La clé est fournie
par l'environnement (`BROXY4G_PROXY_KEY`) ou, à défaut, générée une seule fois
et persistée dans un fichier `0600` du répertoire de données.

Fail-closed : toute erreur de chiffrement/déchiffrement (clé absente/rotée,
valeur corrompue) est journalisée (sans jamais révéler le secret) et lue comme
« non stocké ». Aucune exception ne remonte.
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

KEY_ENV_VAR = "BROXY4G_PROXY_KEY"
KEY_FILENAME = "broxy4g-proxy.key"


class ProxySecretBox:
    """Chiffre/déchiffère de façon réversible le mot de passe proxy au repos."""

    def __init__(self, key: str | None = None, key_path: Path | None = None):
        if key is None:
            key = os.environ.get(KEY_ENV_VAR)
        self._key_path = key_path
        self._fernet: Fernet | None = None
        try:
            self._fernet = Fernet(self._resolve_key(key))
        except Exception:
            # Fail-closed : démarrage jamais bloqué ; la consultation échouera.
            logger.warning(
                "Clé du coffre proxy indisponible : les identifiants chiffrés ne "
                "seront pas consultables (fournir %s valide ou restaurer la clé).",
                KEY_ENV_VAR,
            )

    def _resolve_key(self, key: str | None) -> bytes:
        if key:
            return key.encode("utf-8")
        return self._load_or_create_file_key()

    def _load_or_create_file_key(self) -> bytes:
        assert self._key_path is not None
        if self._key_path.exists():
            return self._key_path.read_bytes()
        token = Fernet.generate_key()
        try:
            fd = os.open(self._key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Course : un autre processus a créé la clé en premier, on la relit.
            return self._key_path.read_bytes()
        with os.fdopen(fd, "wb") as fh:
            fh.write(token)
        logger.warning(
            "Clé du coffre proxy générée localement dans %s : fournir %s en "
            "production pour en contrôler la détention.",
            self._key_path,
            KEY_ENV_VAR,
        )
        return token

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str | None:
        """Chiffre `plaintext` ; ``None`` en échec (jamais d'exception)."""
        if self._fernet is None:
            return None
        try:
            return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        except Exception:
            logger.warning("Échec du chiffrement du secret proxy (jamais loggué)")
            return None

    def decrypt(self, token: str | None) -> str | None:
        """Déchiffre `token` ; ``None`` si absent/corrompu (jamais d'exception)."""
        if self._fernet is None or not token:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError):
            logger.warning(
                "Décodage du secret proxy impossible (clé absente/rotée ou valeur "
                "corrompue) : traité comme non stocké"
            )
            return None
