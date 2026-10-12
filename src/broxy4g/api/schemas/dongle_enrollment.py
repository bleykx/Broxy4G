from pydantic import BaseModel

from broxy4g.api.schemas.dongle import DongleDetail


class EnrollmentProxyCredentials(BaseModel):
    """Identifiants 3proxy du proxy auto-créé, renvoyés en clair une seule fois."""

    username: str
    password: str


class DongleEnrollmentResponse(DongleDetail):
    """Réponse dédiée de ``POST /api/v1/dongles``.

    ``proxy_credentials`` est rempli uniquement si l'enrôlement a réellement
    provisionné un nouveau proxy pendant cet appel ; ``null`` sinon (auto-create
    désactivé ou proxy déjà existant). Ce champ one-time n'apparaît jamais dans
    les réponses GET, qui restent sur le schéma ``DongleDetail``.
    """

    proxy_credentials: EnrollmentProxyCredentials | None = None
