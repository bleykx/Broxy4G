"""Résolveurs de variables d'environnement partagées entre l'API et les workers.

Tous ces résolveurs retombent sur un défaut sûr (jamais d'exception au
démarrage) et journalisent leur repli.
"""
import logging
import os

logger = logging.getLogger(__name__)


def resolve_usb_recovery_cooldown() -> float:
    """Backoff inter-cycles de la récupération USB douce, en secondes.

    Lit ``BROXY4G_USB_RECOVERY_COOLDOWN_SECONDS`` (défaut 300) ; une valeur
    absente, illisible ou négative retombe sur 300 s (jamais de bornage nul
    ou absurde accidentel).
    """
    default = 300.0
    raw = os.environ.get("BROXY4G_USB_RECOVERY_COOLDOWN_SECONDS", "300")
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def resolve_adb_path() -> str:
    """Chemin du binaire ADB (défaut ``adb``).

    Lit ``BROXY4G_ADB_PATH`` ; une valeur absente ou vide retombe sur le
    défaut avec une ligne de journal (jamais d'exception au démarrage).
    """
    default = "adb"
    path = os.environ.get("BROXY4G_ADB_PATH", "").strip()
    if path:
        return path
    logger.info("BROXY4G_ADB_PATH absente ou vide : repli sur « %s »", default)
    return default
