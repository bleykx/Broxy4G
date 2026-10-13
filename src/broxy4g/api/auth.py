import base64
import hmac
import os

from fastapi import HTTPException, Request

API_USER_ENV = "BROXY4G_API_USER"
API_PASS_ENV = "BROXY4G_API_PASSWORD"
QUERY_AUTH_ENV = "BROXY4G_ALLOW_QUERY_AUTH"

# Valeurs (insensibles à la casse) qui activent l'authentification par query string.
# Défaut = désactivé (fail-closed) : la query string est une fuite potentielle
# (proxies, historiques navigateur, logs) et ne doit jamais être lue par défaut.
_QUERY_AUTH_TRUE_VALUES = {"1", "true", "yes", "on"}


def _query_auth_enabled() -> bool:
    """True si l'auth par query string est explicitement activée par env."""
    return os.environ.get(QUERY_AUTH_ENV, "").strip().lower() in _QUERY_AUTH_TRUE_VALUES


def _extract_credentials(request: Request) -> tuple[str, str] | None:
    """Extrait les identifiants à vérifier, ou None si aucune source valide.

    L'en-tête ``Authorization`` est la source primaire : s'il est présent
    (même mal formé), il fait autorité et le fallback query n'est jamais
    consulté. Si l'en-tête est absent et que l'auth par query string est
    activée, les paramètres ``user`` et ``password`` de la query string sont
    utilisés à la place (les deux doivent être présents).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        if not auth_header.startswith("Basic "):
            return None
        try:
            decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode("utf-8")
            username, password = decoded.split(":", 1)
        except Exception:
            return None
        return username, password

    if _query_auth_enabled():
        user = request.query_params.get("user")
        password = request.query_params.get("password")
        if user is not None and password is not None:
            return user, password

    return None


async def verify_basic_auth(request: Request) -> None:
    credentials = _extract_credentials(request)
    if credentials is None:
        raise_unauthorized()

    username, password = credentials

    expected_user = os.environ.get(API_USER_ENV, "admin")
    expected_pass = os.environ.get(API_PASS_ENV)

    if not expected_pass:
        # Password is required for security
        raise HTTPException(status_code=500, detail="API password not configured")

    if not (hmac.compare_digest(username, expected_user) and
            hmac.compare_digest(password, expected_pass)):
        raise_unauthorized()


def raise_unauthorized():
    # We use a custom exception or just HTTPException with headers
    # FastAPI's HTTPException doesn't easily allow adding headers to the response
    # unless we catch it or use a custom handler.
    # However, for Basic Auth, the browser needs the WWW-Authenticate header.
    raise HTTPException(
        status_code=401,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic realm=\"broxy4g\""}
    )
