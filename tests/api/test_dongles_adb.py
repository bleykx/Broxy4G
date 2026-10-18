"""Routes API : transport ADB (enrôlement, découverte, exposition du transport).

Réutilise les fixtures ``client`` / ``auth_headers`` / ``app`` de conftest
(app avec adaptateurs en mémoire). Le sysfs est simulé dans un tmp_path pour
le chemin d'activation complet du téléphone (RNDIS résolus par serial).
"""

from pathlib import Path

from broxy4g.adapters.adb import AdbDevice
from broxy4g.adapters.network_manager import NmIpv4

SERIAL = "SER-001"
IFACE = "enx1234567890ab"


def _build_sysfs(tmp_path: Path, serial: str, iface: str) -> Path:
    """Arborescence sysfs minimale : device USB portant ``serial`` + netdev ``iface`` up."""
    dev = tmp_path / "1-2"
    net_iface = dev / "1-2:1.0" / "net" / iface
    net_iface.mkdir(parents=True)
    (dev / "serial").write_text(serial + "\n")
    (net_iface / "operstate").write_text("up\n")
    (net_iface / "address").write_text("aa:bb:cc:dd:ee:01\n")
    return tmp_path


def _fast_smartphone_uplink(app, tmp_path: Path) -> None:
    """Raccourcit les attentes d'interface/DHCP et branche le sysfs simulé."""
    sysfs_root = _build_sysfs(tmp_path, SERIAL, IFACE)
    app.state.smartphone_uplink.sysfs_root = str(sysfs_root)
    app.state.smartphone_uplink.iface_wait_attempts = 3
    app.state.smartphone_uplink.iface_wait_delay = 0.0
    app.state.smartphone_uplink.dhcp_wait_attempts = 3
    app.state.smartphone_uplink.dhcp_wait_delay = 0.0
    app.state.nm.devices[IFACE] = NmIpv4(address="192.168.172.78", prefix=24)


async def test_enroll_adb_phone_succeeds(client, app, auth_headers, tmp_path):
    _fast_smartphone_uplink(app, tmp_path)

    response = await client.post(
        "/api/v1/dongles",
        json={"label": "Téléphone", "transport": "adb", "identity_adb_serial": SERIAL},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["transport"] == "adb"

    dongle_id = data["id"]
    summary = (await client.get("/api/v1/dongles", headers=auth_headers)).json()
    assert summary[0]["transport"] == "adb"

    detail = (await client.get(f"/api/v1/dongles/{dongle_id}", headers=auth_headers)).json()
    assert detail["transport"] == "adb"
    assert detail["identity_adb_serial"] == SERIAL
    assert detail["state"] == "ONLINE"


async def test_enroll_adb_without_serial_returns_422(client, auth_headers):
    response = await client.post(
        "/api/v1/dongles",
        json={"label": "Téléphone", "transport": "adb"},
        headers=auth_headers,
    )
    assert response.status_code != 500
    assert response.status_code == 422
    assert "numéro de série ADB" in response.json()["detail"]


async def test_enroll_unknown_transport_returns_422(client, auth_headers):
    response = await client.post(
        "/api/v1/dongles",
        json={"identity_imei": "123456789012345", "label": "X", "transport": "cable"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "transport inconnu" in response.json()["detail"]


async def test_enroll_without_any_identifier_returns_422(client, auth_headers):
    response = await client.post(
        "/api/v1/dongles", json={"label": "Sans identité"}, headers=auth_headers
    )
    assert response.status_code == 422
    assert "aucun identifiant" in response.json()["detail"]


async def test_enroll_modem_with_adb_serial_returns_422(client, auth_headers):
    response = await client.post(
        "/api/v1/dongles",
        json={"identity_imei": "123456789012345", "label": "X", "identity_adb_serial": SERIAL},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "transport modem" in response.json()["detail"]


async def test_enroll_modem_flow_default_unchanged(client, auth_headers):
    response = await client.post(
        "/api/v1/dongles",
        json={"identity_imei": "123456789012345", "label": "Dongle 1234"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["transport"] == "modem"

    dongle_id = data["id"]
    summary = (await client.get("/api/v1/dongles", headers=auth_headers)).json()
    assert summary[0]["transport"] == "modem"

    detail = (await client.get(f"/api/v1/dongles/{dongle_id}", headers=auth_headers)).json()
    assert detail["transport"] == "modem"
    assert detail["identity_adb_serial"] is None


async def test_discovered_phone_exposes_adb_fields(client, app, auth_headers):
    app.state.adb.devices.append(
        AdbDevice(serial="SER-999", state="device", model="SM-A217F")
    )

    response = await client.get("/api/v1/dongles/discovered", headers=auth_headers)
    assert response.status_code == 200
    phones = [d for d in response.json() if d["transport"] == "adb"]
    assert len(phones) == 1
    assert phones[0]["identity_adb_serial"] == "SER-999"
    assert phones[0]["model"] == "SM-A217F"
    assert phones[0]["suggested_label"] == "Téléphone SM-A217F"
