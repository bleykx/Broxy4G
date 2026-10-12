"""Tests for RealModemManager with captured mmcli output fixtures."""

from __future__ import annotations

import os

import pytest

from broxy4g.adapters.modem_manager_real import RealModemManager
from broxy4g.adapters.shell_real import FakeShell, ShellResult

# ---------------------------------------------------------------------------
# Fixtures — captured real mmcli output
# ---------------------------------------------------------------------------

MMCLI_L = (
    "/org/freedesktop/ModemManager1/Modem/1 [huawei] E3372\n"
    "    /org/freedesktop/ModemManager1/Modem/0 [huawei] E3372\n"
)

MMCLI_M_0 = """\
--------------------------------
  General  |                 path: /org/freedesktop/ModemManager1/Modem/0
           |            device id: 30650b543495940dc22e0558f59a59fb94d0508f
  --------------------------------
  Hardware |         manufacturer: huawei
           |                model: E3372
           |    firmware revision: 21.180.01.00.00
           |            supported: gsm-umts
           |              current: gsm-umts
           |         equipment id: 867028041311336
  --------------------------------
  System   |               device: /sys/devices/pci0000:00/0000:00:14.0/usb1/1-7
           |              physdev: /sys/devices/pci0000:00/0000:00:14.0/usb1/1-7
           |               drivers: option, huawei_cdc_ncm
           |               plugin: huawei
           |         primary port: cdc-wdm4
           |                ports: cdc-wdm4 (at), ttyUSB2 (at)
  --------------------------------
  Numbers  |                  own: +33768568571
  --------------------------------
  Status   |       unlock retries: sim-pin (3), sim-puk (10), sim-pin2 (3), sim-puk2 (10)
           |                state: registered
           |          power state: on
           |          access tech: lte
           |       signal quality: 51% (recent)
  --------------------------------
  Modes    |            supported: allowed: 2g; preferred: none
           |                       allowed: 3g; preferred: none
           |                       allowed: 4g; preferred: none
           |                       allowed: 2g, 3g, 4g; preferred: none
           |              current: allowed: 2g, 3g, 4g; preferred: none
  --------------------------------
  Bands    |            supported: egsm, dcs, pcs, g850, utran-1, utran-8, eutran-1, eutran-3,
           |                       eutran-7, eutran-8, eutran-20
           |              current: egsm, dcs, pcs, g850, utran-1, utran-8, eutran-1, eutran-3,
           |                       eutran-7, eutran-8, eutran-20
  --------------------------------
  IP       |            supported: ipv4
  --------------------------------
  3GPP     |                 imei: 867028041311336
           |          operator id: 20815
           |        operator name: Free
           |         registration: home
           | packet service state: attached
  --------------------------------
  3GPP EPS | ue mode of operation: csps-2
  --------------------------------
  SIM      |     primary sim path: /org/freedesktop/ModemManager1/SIM/0
"""

MMCLI_M_1 = """\
--------------------------------
  General  |                 path: /org/freedesktop/ModemManager1/Modem/1
           |            device id: aabbccdd11223344556677889900aabbccddeeff
  --------------------------------
  Hardware |         manufacturer: huawei
           |                model: E3372
           |    firmware revision: 21.180.01.00.00
           |            supported: gsm-umts
           |              current: gsm-umts
           |         equipment id: 867028041311195
  --------------------------------
  System   |               device: /sys/devices/pci0000:00/0000:00:14.0/usb1/1-8
           |              physdev: /sys/devices/pci0000:00/0000:00:14.0/usb1/1-8
           |               drivers: option, huawei_cdc_ncm
           |               plugin: huawei
           |         primary port: cdc-wdm5
           |                ports: cdc-wdm5 (at), ttyUSB3 (at)
  --------------------------------
  Numbers  |                  own: +33768568572
  --------------------------------
  Status   |       unlock retries: sim-pin (3), sim-puk (10), sim-pin2 (3), sim-puk2 (10)
           |                state: registered
           |          power state: on
           |          access tech: lte
           |       signal quality: 45% (recent)
  --------------------------------
  Modes    |            supported: allowed: 2g; preferred: none
           |                       allowed: 3g; preferred: none
           |                       allowed: 4g; preferred: none
           |                       allowed: 2g, 3g, 4g; preferred: none
           |              current: allowed: 2g, 3g, 4g; preferred: none
  --------------------------------
  Bands    |            supported: egsm, dcs, pcs, g850, utran-1, utran-8, eutran-1, eutran-3,
           |                       eutran-7, eutran-8, eutran-20
           |              current: egsm, dcs, pcs, g850, utran-1, utran-8, eutran-1, eutran-3,
           |                       eutran-7, eutran-8, eutran-20
  --------------------------------
  IP       |            supported: ipv4
  --------------------------------
  3GPP     |                 imei: 867028041311195
           |          operator id: 20815
           |        operator name: Free
           |         registration: home
           | packet service state: attached
  --------------------------------
  3GPP EPS | ue mode of operation: csps-2
  --------------------------------
  SIM      |     primary sim path: /org/freedesktop/ModemManager1/SIM/1
"""

MMCLI_SIM_0 = """\
-------------------------------
  General    |              path: /org/freedesktop/ModemManager1/SIM/0
  -------------------------------
  Properties |            active: yes
             |              imsi: 208150043462938
             |             iccid: 8933150224041411388
             |       operator id: 20815
             |     operator name: Free
             |              gid1: FFFFFFFF
             |              gid2: FFFFFFFF
"""

MMCLI_SIM_1 = """\
-------------------------------
  General    |              path: /org/freedesktop/ModemManager1/SIM/1
  -------------------------------
  Properties |            active: yes
              |              imsi: 208150043462939
              |             iccid: 8933150224041411399
              |     operator name: Free
              |              gid1: FFFFFFFF
              |              gid2: FFFFFFFF
"""

MMCLI_SIGNAL_GET_LTE = """\
modem.signal.refresh.rate         : 30
modem.signal.lte.rssi             : -63
modem.signal.lte.rsrq             : -11
modem.signal.lte.rsrp             : -92
modem.signal.lte.snr              : 9
"""

MMCLI_SIGNAL_GET_DISABLED = """\
modem.signal.refresh.rate         : 0
modem.signal.lte.rssi             : --
modem.signal.lte.rsrq             : --
modem.signal.lte.rsrp             : --
modem.signal.lte.snr              : --
"""

MMCLI_SIGNAL_GET_DASHES = """\
modem.signal.refresh.rate         : 30
modem.signal.lte.rssi             : --
modem.signal.lte.rsrq             : --
modem.signal.lte.rsrp             : --
modem.signal.lte.snr              : --
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_shell():
    shell = FakeShell()
    # mmcli -L
    shell.set_response(["mmcli", "-L"], ShellResult(0, MMCLI_L, ""))
    # mmcli -m /Modem/0
    shell.set_response(["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0"],
                       ShellResult(0, MMCLI_M_0, ""))
    # mmcli -m /Modem/1
    shell.set_response(["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/1"],
                       ShellResult(0, MMCLI_M_1, ""))
    # mmcli -i /SIM/0
    shell.set_response(["mmcli", "-i", "/org/freedesktop/ModemManager1/SIM/0"],
                       ShellResult(0, MMCLI_SIM_0, ""))
    # mmcli -i /SIM/1
    shell.set_response(["mmcli", "-i", "/org/freedesktop/ModemManager1/SIM/1"],
                       ShellResult(0, MMCLI_SIM_1, ""))
    return shell


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListModems:
    async def test_returns_two_modems(self, fake_shell):
        m = RealModemManager(fake_shell)
        modems = await m.list_modems()
        assert len(modems) == 2

    async def test_paths_and_imei(self, fake_shell):
        m = RealModemManager(fake_shell)
        modems = await m.list_modems()
        paths = [x.path for x in modems]
        assert "/org/freedesktop/ModemManager1/Modem/0" in paths
        assert "/org/freedesktop/ModemManager1/Modem/1" in paths
        imei_map = {x.path: x.imei for x in modems}
        assert imei_map["/org/freedesktop/ModemManager1/Modem/0"] == "867028041311336"
        assert imei_map["/org/freedesktop/ModemManager1/Modem/1"] == "867028041311195"


class TestGetModem:
    async def test_parses_state(self, fake_shell):
        m = RealModemManager(fake_shell)
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert modem.state == "registered"

    async def test_parses_device_path(self, fake_shell):
        m = RealModemManager(fake_shell)
        # The System "device:" path from the mmcli fixture must be parsed and
        # handed to the sysfs resolvers (host sysfs is not available in tests).
        seen: list[str | None] = []

        def capture(device_path: str | None) -> str | None:
            seen.append(device_path)
            return None

        def no_serial(device_path: str | None) -> str | None:
            return None

        m._resolve_iface_from_sysfs = capture
        m._read_usb_serial_from_sysfs = no_serial
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert seen == ["/sys/devices/pci0000:00/0000:00:14.0/usb1/1-7"]
        assert modem.interface_name is None

    async def test_parses_usb_serial_with_monkeypatch(self, fake_shell):
        m = RealModemManager(fake_shell)
        # Monkeypatch the sysfs reader
        m._read_usb_serial_from_sysfs = lambda dp: "TEST_SERIAL_0"
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert modem.usb_serial == "TEST_SERIAL_0"

    async def test_parses_interface_with_monkeypatch(self, fake_shell):
        m = RealModemManager(fake_shell)
        m._resolve_iface_from_sysfs = lambda dp: "wwan0"
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert modem.interface_name == "wwan0"

    async def test_iccid_from_sim(self, fake_shell):
        m = RealModemManager(fake_shell)
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert modem.iccid == "8933150224041411388"

    async def test_imsi_from_sim(self, fake_shell):
        m = RealModemManager(fake_shell)
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert modem.imsi == "208150043462938"

    async def test_operator_from_3gpp(self, fake_shell):
        m = RealModemManager(fake_shell)
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/0")
        assert modem is not None
        assert modem.operator_id == "20815"
        assert modem.operator_name == "Free"

    async def test_operator_fallback_from_sim(self, fake_shell):
        # SIM/1 has no operator id, only operator name: 3GPP still provides both.
        m = RealModemManager(fake_shell)
        modem = await m.get_modem("/org/freedesktop/ModemManager1/Modem/1")
        assert modem is not None
        assert modem.imsi == "208150043462939"
        assert modem.operator_name == "Free"


class TestConnect:
    async def test_connect_with_apn(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
             "--simple-connect=apn=free"],
            ShellResult(0, "", ""),
        )
        m = RealModemManager(fake_shell)
        await m.connect("/org/freedesktop/ModemManager1/Modem/0", "free", None)
        calls = fake_shell.calls
        cmd, timeout = calls[-1]
        assert cmd == ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
                       "--simple-connect=apn=free"]
        assert timeout == 120

    async def test_connect_with_apn_and_auth(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
             "--simple-connect=apn=free,auth=pap"],
            ShellResult(0, "", ""),
        )
        m = RealModemManager(fake_shell)
        await m.connect("/org/freedesktop/ModemManager1/Modem/0", "free", "pap")
        calls = fake_shell.calls
        cmd, _ = calls[-1]
        assert cmd == ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
                       "--simple-connect=apn=free,auth=pap"]

    async def test_connect_bare_when_no_apn(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
             "--simple-connect"],
            ShellResult(0, "", ""),
        )
        m = RealModemManager(fake_shell)
        await m.connect("/org/freedesktop/ModemManager1/Modem/0", "", None)
        calls = fake_shell.calls
        cmd, _ = calls[-1]
        assert cmd == ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
                       "--simple-connect"]

    async def test_connect_timeout_120(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
             "--simple-connect=apn=apn"],
            ShellResult(0, "", ""),
        )
        m = RealModemManager(fake_shell)
        await m.connect("/org/freedesktop/ModemManager1/Modem/0", "apn", None)
        calls = fake_shell.calls
        _, timeout = calls[-1]
        assert timeout == 120

    async def test_connect_raises_on_failure(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
             "--simple-connect=apn=free"],
            ShellResult(1, "", "PolicyKit authorization failed"),
        )
        m = RealModemManager(fake_shell)
        with pytest.raises(RuntimeError) as exc:
            await m.connect("/org/freedesktop/ModemManager1/Modem/0", "free", None)
        assert "rc=1" in str(exc.value)
        assert "PolicyKit authorization failed" in str(exc.value)


class TestDisconnect:
    async def test_uses_simple_disconnect(self, fake_shell):
        # Aucun bearer rattaché : la boucle d'attente retombe immédiatement.
        fake_shell.set_response(
            ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0", "-K"],
            ShellResult(0, "modem.state                 : disabled\n", ""),
        )
        m = RealModemManager(fake_shell)
        await m.disconnect("/org/freedesktop/ModemManager1/Modem/0")
        calls = fake_shell.calls
        cmd, _ = calls[0]
        assert cmd == ["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/0",
                       "--simple-disconnect"]
        assert not any("-b" in c for c, _ in calls)  # aucun bearer à inspecter


class TestResetModem:
    MODEM = "/org/freedesktop/ModemManager1/Modem/0"

    async def test_reset_runs_mmcli_reset(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", self.MODEM, "--reset"], ShellResult(0, "", ""),
        )
        m = RealModemManager(fake_shell)
        await m.reset_modem(self.MODEM)
        cmd, timeout = fake_shell.calls[-1]
        assert cmd == ["mmcli", "-m", self.MODEM, "--reset"]
        assert timeout == 60

    async def test_reset_raises_on_failure(self, fake_shell):
        fake_shell.set_response(
            ["mmcli", "-m", self.MODEM, "--reset"],
            ShellResult(1, "", "unable to reset modem"),
        )
        m = RealModemManager(fake_shell)
        with pytest.raises(RuntimeError) as exc:
            await m.reset_modem(self.MODEM)
        assert "rc=1" in str(exc.value)
        assert "unable to reset modem" in str(exc.value)


MMCLI_M_WITH_BEARER = """\
modem.generic.bearers.value[0]    : /org/freedesktop/ModemManager1/Bearer/0
"""

BEARER_K_CONNECTED = (
    "bearer.status.connected         : yes\n"
    "bearer.ipv4-config.address      : 10.0.0.2\n"
    "bearer.ipv4-config.prefix       : 30\n"
)

BEARER_K_DISCONNECTED = "bearer.status.connected         : no\n"


class BearerStateShell:
    """Shell simulé : le bearer reste connecté N lectures ``-b`` avant de tomber.

    ``connected_reads=-1`` : le bearer ne tombe jamais.
    """

    def __init__(self, connected_reads: int, disconnect_rc: int = 0):
        self.calls: list[tuple[list[str], float]] = []
        self._connected_reads = connected_reads
        self._disconnect_rc = disconnect_rc
        self._reads = 0

    async def run(self, command: list[str], timeout: float = 30.0) -> ShellResult:
        self.calls.append((command, timeout))
        if "--simple-disconnect" in command:
            return ShellResult(self._disconnect_rc, "", "")
        if "-b" in command:
            self._reads += 1
            connected = self._connected_reads < 0 or self._reads <= self._connected_reads
            return ShellResult(0, BEARER_K_CONNECTED if connected else BEARER_K_DISCONNECTED, "")
        if "-m" in command and "-K" in command:
            return ShellResult(0, MMCLI_M_WITH_BEARER, "")
        return ShellResult(1, "", "Command not found")


class TestDisconnectBearerWait:
    MODEM = "/org/freedesktop/ModemManager1/Modem/0"

    async def test_waits_until_bearer_drops(self):
        """Le disconnect n'arrive que quand le bearer a effectivement basculé."""
        shell = BearerStateShell(connected_reads=2)
        m = RealModemManager(shell, disconnect_wait_attempts=5, disconnect_wait_delay=0.001)
        await m.disconnect(self.MODEM)  # ne lève pas
        bearer_reads = [c for c in shell.calls if "-b" in c[0]]
        assert len(bearer_reads) == 3  # 2 lectures connectées, puis 1 lecture tombée

    async def test_raises_when_bearer_stays_connected(self):
        """Au-delà des tentatives, un bearer toujours actif lève une erreur explicite."""
        shell = BearerStateShell(connected_reads=-1)
        m = RealModemManager(shell, disconnect_wait_attempts=2, disconnect_wait_delay=0.001)
        with pytest.raises(RuntimeError, match="toujours actif"):
            await m.disconnect(self.MODEM)

    async def test_tolerates_nonzero_rc_when_bearer_gone(self):
        """rc != 0 (déjà déconnecté) est toléré si le bearer a effectivement disparu."""
        shell = BearerStateShell(connected_reads=0, disconnect_rc=1)
        m = RealModemManager(shell, disconnect_wait_attempts=3, disconnect_wait_delay=0.001)
        await m.disconnect(self.MODEM)  # ne lève pas


class TestGetSessionId:
    MODEM = "/org/freedesktop/ModemManager1/Modem/0"
    BEARER = "/org/freedesktop/ModemManager1/Bearer/0"

    async def test_returns_path_of_connected_bearer(self, fake_shell):
        fake_shell.set_response(["mmcli", "-m", self.MODEM, "-K"],
                                ShellResult(0, MMCLI_M_WITH_BEARER, ""))
        fake_shell.set_response(["mmcli", "-b", self.BEARER, "-K"],
                                ShellResult(0, BEARER_K_CONNECTED, ""))
        m = RealModemManager(fake_shell)
        assert await m.get_session_id(self.MODEM) == self.BEARER

    async def test_returns_none_without_bearer(self, fake_shell):
        m = RealModemManager(fake_shell)
        assert await m.get_session_id(self.MODEM) is None


class TestGetSignal:
    MODEM = "/org/freedesktop/ModemManager1/Modem/0"

    def _register_signal_get(self, fake_shell, fixture):
        fake_shell.set_response(
            ["mmcli", "-m", self.MODEM, "--signal-get", "-K"],
            ShellResult(0, fixture, ""),
        )

    async def test_returns_percent(self, fake_shell):
        m = RealModemManager(fake_shell)
        signal = await m.get_signal("/org/freedesktop/ModemManager1/Modem/0")
        assert signal.percent == 51
        assert signal.rsrp is None
        assert signal.rsrq is None
        assert signal.sinr is None
        assert signal.rssi is None

    async def test_returns_none_on_failure(self, fake_shell):
        fake_shell.set_response(["mmcli", "-m", "/org/freedesktop/ModemManager1/Modem/99"],
                                ShellResult(1, "", "error"))
        m = RealModemManager(fake_shell)
        signal = await m.get_signal("/org/freedesktop/ModemManager1/Modem/99")
        assert signal.percent is None

    async def test_parses_detailed_lte_signal(self, fake_shell):
        self._register_signal_get(fake_shell, MMCLI_SIGNAL_GET_LTE)
        m = RealModemManager(fake_shell)
        signal = await m.get_signal(self.MODEM)
        assert signal.percent == 51
        assert signal.rsrp == -92
        assert signal.rsrq == -11
        assert signal.sinr == 9
        assert signal.rssi == -63

    async def test_enables_polling_when_disabled(self, fake_shell):
        self._register_signal_get(fake_shell, MMCLI_SIGNAL_GET_DISABLED)
        fake_shell.set_response(
            ["mmcli", "-m", self.MODEM, "--signal-setup", "30"],
            ShellResult(0, "", ""),
        )
        m = RealModemManager(fake_shell)
        signal = await m.get_signal(self.MODEM)
        setup_cmd = ["mmcli", "-m", self.MODEM, "--signal-setup", "30"]
        assert any(cmd == setup_cmd for cmd, _ in fake_shell.calls)
        assert signal.rsrp is None
        assert signal.rsrq is None
        assert signal.sinr is None
        assert signal.rssi is None
        assert signal.percent == 51

    async def test_no_signal_setup_when_already_enabled(self, fake_shell):
        self._register_signal_get(fake_shell, MMCLI_SIGNAL_GET_LTE)
        m = RealModemManager(fake_shell)
        await m.get_signal(self.MODEM)
        assert not any("--signal-setup" in cmd for cmd, _ in fake_shell.calls)

    async def test_dashes_map_to_none(self, fake_shell):
        self._register_signal_get(fake_shell, MMCLI_SIGNAL_GET_DASHES)
        m = RealModemManager(fake_shell)
        signal = await m.get_signal(self.MODEM)
        assert signal.percent == 51
        assert signal.rsrp is None
        assert signal.rsrq is None
        assert signal.sinr is None
        assert signal.rssi is None


class TestGetImei:
    async def test_returns_imei(self, fake_shell):
        m = RealModemManager(fake_shell)
        imei = await m.get_imei("/org/freedesktop/ModemManager1/Modem/0")
        assert imei == "867028041311336"


class TestResolveIfaceFromSysfs:
    """Frontière de chemin : `.../1-1.2.4.3` ne doit pas matcher `.../1-1.2.4.30`."""

    BASE = "/sys/devices/pci0000:00/0000:00:14.0/usb1"

    def _patch_sysfs(self, monkeypatch, entries: dict[str, str]) -> None:
        monkeypatch.setattr(os, "listdir", lambda path: sorted(entries))
        monkeypatch.setattr(
            os.path, "realpath", lambda path: entries[os.path.basename(path)]
        )

    def test_returns_attached_netdev(self, monkeypatch):
        self._patch_sysfs(monkeypatch, {
            "eth0": "/sys/devices/pci0000:00/0000:00:1f.6/virtio1/net/eth0",
            "wwan0": f"{self.BASE}/1-1.2.4.3/net/wwan0",
        })
        result = RealModemManager._resolve_iface_from_sysfs(f"{self.BASE}/1-1.2.4.3")
        assert result == "wwan0"

    def test_sibling_path_prefix_does_not_match(self, monkeypatch):
        """Un netdev rattaché au sibling `1-1.2.4.30` n'est pas retourné pour `1-1.2.4.3`."""
        self._patch_sysfs(monkeypatch, {
            "wwx0": f"{self.BASE}/1-1.2.4.30/net/wwx0",
        })
        result = RealModemManager._resolve_iface_from_sysfs(f"{self.BASE}/1-1.2.4.3")
        assert result is None

    def test_sibling_still_matches_its_own_device(self, monkeypatch):
        self._patch_sysfs(monkeypatch, {
            "wwan0": f"{self.BASE}/1-1.2.4.3/net/wwan0",
            "wwx0": f"{self.BASE}/1-1.2.4.30/net/wwx0",
        })
        result = RealModemManager._resolve_iface_from_sysfs(f"{self.BASE}/1-1.2.4.30")
        assert result == "wwx0"

    def test_exact_path_matches(self, monkeypatch):
        self._patch_sysfs(monkeypatch, {
            "wwan0": f"{self.BASE}/1-1.2.4.3",
        })
        result = RealModemManager._resolve_iface_from_sysfs(f"{self.BASE}/1-1.2.4.3")
        assert result == "wwan0"

    def test_deterministic_order(self, monkeypatch):
        """L'itération est triée : le premier netdev en ordre alphabétique gagne."""
        self._patch_sysfs(monkeypatch, {
            "wwx9": f"{self.BASE}/1-1.2.4.3/net/wwx9",
            "wwan1": f"{self.BASE}/1-1.2.4.3/net/wwan1",
        })
        result = RealModemManager._resolve_iface_from_sysfs(f"{self.BASE}/1-1.2.4.3")
        assert result == "wwan1"

    def test_none_without_device_path(self):
        assert RealModemManager._resolve_iface_from_sysfs(None) is None


BEARER_K_PPP = (
    "bearer.status.connected         : yes\n"
    "bearer.ipv4-config.method       : ppp\n"
    "bearer.ipv4-config.address      : --\n"
    "bearer.ipv4-config.prefix       : --\n"
    "bearer.ipv4-config.gateway      : --\n"
    "bearer.status.interface         : --\n"
)


class TestGetBearerIp:
    MODEM = "/org/freedesktop/ModemManager1/Modem/0"
    BEARER = "/org/freedesktop/ModemManager1/Bearer/0"

    def _register_bearer(self, fake_shell, output: str) -> None:
        fake_shell.set_response(["mmcli", "-m", self.MODEM, "-K"],
                                ShellResult(0, MMCLI_M_WITH_BEARER, ""))
        fake_shell.set_response(["mmcli", "-b", self.BEARER, "-K"],
                                ShellResult(0, output, ""))

    async def test_ppp_session_surfaces_method(self, fake_shell):
        """Une session PPP sans adresse remonte ``method="ppp"`` (diagnostic)."""
        self._register_bearer(fake_shell, BEARER_K_PPP)
        m = RealModemManager(fake_shell)
        config = await m.get_bearer_ip(self.MODEM)
        assert config is not None
        assert config.method == "ppp"
        assert config.address == ""

    async def test_static_session_has_no_method(self, fake_shell):
        self._register_bearer(fake_shell, BEARER_K_CONNECTED)
        m = RealModemManager(fake_shell)
        config = await m.get_bearer_ip(self.MODEM)
        assert config is not None
        assert config.method is None
        assert config.address == "10.0.0.2"


class TestGetIccid:
    async def test_returns_iccid(self, fake_shell):
        m = RealModemManager(fake_shell)
        iccid = await m.get_iccid("/org/freedesktop/ModemManager1/Modem/0")
        assert iccid == "8933150224041411388"
