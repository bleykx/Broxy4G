"""Tests du modèle de domaine ProxyPool (plage de ports, defaults, touch)."""

from datetime import UTC, datetime, timedelta

from broxy4g.domain.proxy_network import PROXY_PORT_END, PROXY_PORT_START
from broxy4g.domain.proxy_pool import POOL_PORT_END, POOL_PORT_START, ProxyPool


def test_pool_port_range_constants():
    assert POOL_PORT_START == 31301
    assert POOL_PORT_END == 31399


def test_pool_port_range_disjoint_from_proxy_range():
    # Plage des pools strictement au-dessus de la plage des proxies.
    assert POOL_PORT_START > PROXY_PORT_END
    assert POOL_PORT_START > PROXY_PORT_START


def test_proxy_pool_defaults():
    pool = ProxyPool(label="p", listen_port=POOL_PORT_START)
    assert pool.enabled is True
    assert pool.auth_user is None
    assert pool.password_encrypted is None
    assert pool.created_at.tzinfo is not None
    assert pool.updated_at.tzinfo is not None


def test_two_instances_have_unique_ids():
    a = ProxyPool(label="p", listen_port=31301)
    b = ProxyPool(label="p", listen_port=31302)
    assert a.id != b.id


def test_touch_advances_updated_at():
    """Forcer updated_at dans le passé, puis vérifier que touch() l'avance."""
    pool = ProxyPool(label="p", listen_port=31301)
    pool.updated_at = datetime.now(UTC) - timedelta(seconds=10)
    pool.touch()
    assert pool.updated_at > datetime.now(UTC) - timedelta(milliseconds=500)
