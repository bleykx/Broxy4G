import pytest

from broxy4g.repositories.settings_repository import AUTO_CREATE_PROXY_KEY


@pytest.mark.asyncio
async def test_settings_set_get(settings_repo):
    assert await settings_repo.get("missing") is None
    await settings_repo.set("some.key", "value")
    assert await settings_repo.get("some.key") == "value"


@pytest.mark.asyncio
async def test_settings_get_default(settings_repo):
    assert await settings_repo.get("missing", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_settings_set_overwrites(settings_repo):
    await settings_repo.set("k", "v1")
    await settings_repo.set("k", "v2")
    assert await settings_repo.get("k") == "v2"


@pytest.mark.asyncio
async def test_settings_get_bool_default(settings_repo):
    assert await settings_repo.get_bool("missing", True) is True
    assert await settings_repo.get_bool("missing", False) is False


@pytest.mark.asyncio
async def test_settings_set_get_bool(settings_repo):
    await settings_repo.set_bool("flag", True)
    assert await settings_repo.get_bool("flag", False) is True
    assert await settings_repo.get("flag") == "1"

    await settings_repo.set_bool("flag", False)
    assert await settings_repo.get_bool("flag", True) is False
    assert await settings_repo.get("flag") == "0"


@pytest.mark.asyncio
async def test_settings_auto_create_proxy_key(settings_repo):
    assert AUTO_CREATE_PROXY_KEY == "proxy.auto_create"
    assert await settings_repo.get_bool(AUTO_CREATE_PROXY_KEY, True) is True
    await settings_repo.set_bool(AUTO_CREATE_PROXY_KEY, False)
    assert await settings_repo.get_bool(AUTO_CREATE_PROXY_KEY, True) is False
