"""Chemins et écriture disque des fichiers d'instance d'un proxy.

Chaque proxy possède son propre répertoire `/var/lib/broxy4g/proxies/<id>/`
contenant son `3proxy.cfg` et son `docker-compose.yml`. Cette séparation
permet de recréer ou redémarrer un proxy sans toucher aux autres.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

_CONFIG_MODE = 0o644
_DIR_MODE = 0o750


@dataclass(frozen=True)
class ProxyPaths:
    base_dir: Path

    def proxy_dir(self, proxy_id: UUID) -> Path:
        return Path(self.base_dir) / "proxies" / str(proxy_id)

    def config_path(self, proxy_id: UUID) -> Path:
        return self.proxy_dir(proxy_id) / "3proxy.cfg"

    def compose_path(self, proxy_id: UUID) -> Path:
        return self.proxy_dir(proxy_id) / "docker-compose.yml"

    def compose_dir(self, proxy_id: UUID) -> str:
        return str(self.proxy_dir(proxy_id))


class ProxyFileWriter:
    def __init__(self, paths: ProxyPaths):
        self.paths = paths

    def _ensure_dir(self, proxy_id: UUID) -> Path:
        directory = self.paths.proxy_dir(proxy_id)
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, _DIR_MODE)
        return directory

    def _atomic_write(self, path: Path, content: str) -> Path:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.chmod(tmp_name, _CONFIG_MODE)
            os.replace(tmp_name, path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return path

    def write_config(self, proxy_id: UUID, content: str) -> Path:
        self._ensure_dir(proxy_id)
        return self._atomic_write(self.paths.config_path(proxy_id), content)

    def write_compose(self, proxy_id: UUID, content: str) -> Path:
        self._ensure_dir(proxy_id)
        return self._atomic_write(self.paths.compose_path(proxy_id), content)

    def remove_proxy_dir(self, proxy_id: UUID) -> None:
        shutil.rmtree(self.paths.proxy_dir(proxy_id), ignore_errors=True)
