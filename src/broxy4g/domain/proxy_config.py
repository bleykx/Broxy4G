"""Spécification et rendu de la configuration 3proxy d'un conteneur."""

from dataclasses import dataclass

INTERNAL_PROXY_PORT = 3128


@dataclass(frozen=True)
class ProxyConfigSpec:
    username: str
    password_hash: str
    allowed_sources: tuple[str, ...]  # IP/CIDR/plages déjà normalisées, non vide
    listen_port: int = INTERNAL_PROXY_PORT
    dns_servers: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")
    max_connections: int = 128


def render_3proxy_config(spec: ProxyConfigSpec) -> str:
    """Rend le fichier de configuration 3proxy (syntaxe vérifiée).

    Le token `users` est quoté : le hash `CR` contient des `$` que 3proxy
    interpréterait comme des includes sans les guillemets.
    """
    if not spec.allowed_sources:
        raise ValueError("allowed_sources ne peut pas être vide")

    lines: list[str] = []
    for server in spec.dns_servers:
        lines.append(f"nserver {server}")
    lines.append("nscache 65536")
    lines.append("auth strong")
    lines.append(f'users "{spec.username}:CR:{spec.password_hash}"')
    lines.append(f"allow {spec.username} {','.join(spec.allowed_sources)} * * *")
    lines.append("deny *")
    lines.append(f"maxconn {spec.max_connections}")
    lines.append(f"proxy -p{spec.listen_port}")
    return "\n".join(lines) + "\n"
