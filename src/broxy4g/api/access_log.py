"""Access log uvicorn avec redaction des secrets de la query string.

Pourquoi un middleware FastAPI ne suffit pas : l'access log est émis par le
serveur uvicorn *avant* les middlewares FastAPI, et sa ligne de requête
inclut le ``full_path`` (chemin + query string) via la variable ``request_line``.
Aucun middleware ni route ne peut donc modifier cette ligne après coup. On
sous-classe le ``AccessFormatter`` d'uvicorn et on masque les paires
clé=valeur sensibles dans le message final produit.
"""

import copy
import re

import uvicorn.config
import uvicorn.logging

# Paires clé=valeur à masquer dans le message final (insensible à la casse).
# La valeur s'arrête au premier séparateur de query string, espace ou guillemet.
# ``key`` après ``api_key|apikey`` : le match le plus à gauche gagne, donc
# ``apikey=...`` est bien masqué entier.
_SENSITIVE_KEY_PATTERN = r"password|passwd|token|secret|api_key|apikey|key|user|username"
_REDACT_RE = re.compile(
    rf"({_SENSITIVE_KEY_PATTERN})=([^&\s\"']*)",
    re.IGNORECASE,
)
_REDACTED_VALUE = "***"


class RedactingAccessFormatter(uvicorn.logging.AccessFormatter):
    """``AccessFormatter`` d'uvicorn qui masque les secrets du message produit."""

    def format(self, record):
        message = super().format(record)
        # ``password=***`` est déjà masqué : la regex le remplace par lui-même.
        return _REDACT_RE.sub(rf"\1={_REDACTED_VALUE}", message)


def build_access_log_config() -> dict:
    """Config de logging uvicorn avec le formateur access redigeant.

    Copie profonde de ``uvicorn.config.LOGGING_CONFIG`` : le formateur
    ``access`` passe sur ``RedactingAccessFormatter`` (les clés ``fmt`` et
    ``use_colors`` éventuelles sont conservées).
    """
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    access_formatter = dict(config["formatters"]["access"])
    access_formatter["()"] = RedactingAccessFormatter
    config["formatters"]["access"] = access_formatter
    return config
