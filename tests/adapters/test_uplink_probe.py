"""Verdict tri-état de la sonde d'IP publique (UplinkProbe.public_ip_verdict).

Distingue les deux échecs que ``public_ip()`` confond en ``None`` :
``NOT_RUN`` (pas d'IP source sur l'interface) vs ``FAILED`` (curl échoué).
"""

from broxy4g.adapters.connectivity import FakeConnectivity
from broxy4g.adapters.net_interface import FakeInterface
from broxy4g.domain.degraded_arbiter import ProbeVerdict
from broxy4g.services.uplink_probe import PublicIpVerdict, UplinkProbe


def _probe(connectivity: FakeConnectivity | None = None) -> tuple[UplinkProbe, FakeInterface]:
    iface = FakeInterface()
    probe = UplinkProbe(iface, connectivity or FakeConnectivity())
    return probe, iface


async def test_verdict_ok_when_public_ip_obtained():
    probe, iface = _probe()
    connectivity = probe.connectivity
    connectivity.set_public_ip("1.2.3.4")
    iface.set_address("wwan0", "10.0.0.1")

    result = await probe.public_ip_verdict("wwan0")

    assert result == PublicIpVerdict(ProbeVerdict.OK, "1.2.3.4")


async def test_verdict_failed_when_curl_fails():
    probe, iface = _probe()  # pas d'IP publique configurée → curl « échoue »
    iface.set_address("wwan0", "10.0.0.1")

    result = await probe.public_ip_verdict("wwan0")

    assert result == PublicIpVerdict(ProbeVerdict.FAILED, None)


async def test_verdict_not_run_when_no_source_ip():
    probe, iface = _probe()
    # L'interface existe mais n'a aucune IP source (ou pas d'interface du tout).
    result = await probe.public_ip_verdict("wwan0")
    assert result == PublicIpVerdict(ProbeVerdict.NOT_RUN, None)
    assert await probe.public_ip_verdict(None) == PublicIpVerdict(ProbeVerdict.NOT_RUN, None)


async def test_public_ip_semantics_unchanged():
    """``public_ip()`` continue de renvoyer ``str | None`` (consommateurs existants)."""
    probe, iface = _probe()
    iface.set_address("wwan0", "10.0.0.1")

    assert await probe.public_ip("wwan0") is None  # curl échoué

    probe.connectivity.set_public_ip("1.2.3.4")
    assert await probe.public_ip("wwan0") == "1.2.3.4"
