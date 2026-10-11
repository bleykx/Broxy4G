from uuid import uuid4

from broxy4g.domain.proxy import Proxy, ProxyState


def test_proxy():
    # Test création Proxy avec état STOPPED par défaut
    proxy = Proxy(dongle_id=uuid4(), container_name="test_proxy", listen_port=8080)
    assert proxy.state == ProxyState.STOPPED
    
    # Test touch met à jour updated_at
    old_updated_at = proxy.updated_at
    proxy.touch()
    assert proxy.updated_at != old_updated_at

if __name__ == "__main__":
    test_proxy()
    print("All tests passed!")