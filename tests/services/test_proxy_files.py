import os
from uuid import uuid4

from broxy4g.services.proxy_files import ProxyFileWriter, ProxyPaths


def test_paths_layout(tmp_path):
    paths = ProxyPaths(tmp_path)
    proxy_id = uuid4()
    assert paths.proxy_dir(proxy_id) == tmp_path / "proxies" / str(proxy_id)
    assert paths.config_path(proxy_id) == paths.proxy_dir(proxy_id) / "3proxy.cfg"
    assert paths.compose_path(proxy_id) == paths.proxy_dir(proxy_id) / "docker-compose.yml"
    assert paths.compose_dir(proxy_id) == str(paths.proxy_dir(proxy_id))


def test_write_config_creates_file_with_expected_mode(tmp_path):
    writer = ProxyFileWriter(ProxyPaths(tmp_path))
    proxy_id = uuid4()

    path = writer.write_config(proxy_id, "hello\n")

    assert path.read_text() == "hello\n"
    assert os.stat(path).st_mode & 0o777 == 0o644
    assert os.stat(path.parent).st_mode & 0o777 == 0o750


def test_write_is_idempotent(tmp_path):
    writer = ProxyFileWriter(ProxyPaths(tmp_path))
    proxy_id = uuid4()

    writer.write_config(proxy_id, "v1")
    writer.write_config(proxy_id, "v2")

    assert writer.paths.config_path(proxy_id).read_text() == "v2"


def test_write_compose(tmp_path):
    writer = ProxyFileWriter(ProxyPaths(tmp_path))
    proxy_id = uuid4()

    path = writer.write_compose(proxy_id, "services: {}\n")

    assert path == writer.paths.compose_path(proxy_id)
    assert path.read_text() == "services: {}\n"


def test_remove_proxy_dir_is_idempotent(tmp_path):
    writer = ProxyFileWriter(ProxyPaths(tmp_path))
    proxy_id = uuid4()
    writer.write_config(proxy_id, "x")

    writer.remove_proxy_dir(proxy_id)
    assert not writer.paths.proxy_dir(proxy_id).exists()

    writer.remove_proxy_dir(proxy_id)  # ne lève pas si déjà supprimé
