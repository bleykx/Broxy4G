"""Réparation des lignes proxy sans `password_hash` (pré-migration).

Une ligne créée avant la migration de la colonne `password_hash` (voir
`repositories/database.py`) ne porte aucun hash MD5-crypt exploitable : ce
hash est irréversible, le proxy ne peut pas servir (le cfg 3proxy serait
`users ":CR:"`, invalide) et la seule réparation est de générer un nouveau
jeu d'identifiants.
"""

import logging

from broxy4g.domain.proxy import Proxy
from broxy4g.domain.proxy_credentials import generate_credentials

logger = logging.getLogger(__name__)


def ensure_credentials(proxy: Proxy) -> bool:
    """Génère de nouveaux identifiants si `password_hash` est vide.

    Sans effet si le proxy porte déjà un hash. Retourne ``True`` si une
    réparation a été effectuée (les anciens identifiants sont perdus et les
    clients doivent être reconfigurés), ``False`` sinon.
    """
    if proxy.password_hash:
        return False
    credentials = generate_credentials()
    proxy.auth_user = credentials.username
    proxy.password_hash = credentials.password_hash
    logger.warning(
        "Proxy %s sans hash de mot de passe (ligne antérieure à la migration "
        "password_hash) : nouveaux identifiants générés automatiquement et "
        "jamais affichés (réparation hors API) ; déclencher une rotation "
        "explicite pour obtenir le mot de passe et reconfigurer les clients",
        proxy.id,
    )
    return True
