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
from broxy4g.services.proxy_secret_box import ProxySecretBox

logger = logging.getLogger(__name__)


def ensure_credentials(proxy: Proxy, secret_box: ProxySecretBox | None = None) -> bool:
    """Génère de nouveaux identifiants si `password_hash` est vide.

    Sans effet si le proxy porte déjà un hash. Le clair du mot de passe est
    chiffré au repos via le coffre (si fourni), pour rester consultable via
    l'API. Retourne ``True`` si une réparation a été effectuée, ``False``
    sinon.
    """
    if proxy.password_hash:
        return False
    credentials = generate_credentials()
    proxy.auth_user = credentials.username
    proxy.password_hash = credentials.password_hash
    if secret_box is not None:
        proxy.password_encrypted = secret_box.encrypt(credentials.password)
    logger.warning(
        "Proxy %s sans hash de mot de passe (ligne antérieure à la migration "
        "password_hash) : nouveaux identifiants générés automatiquement ; ils "
        "sont consultables via l'API si le coffre est opérant, sinon une "
        "rotation explicite est recommandée pour les récupérer et "
        "reconfigurer les clients",
        proxy.id,
    )
    return True
