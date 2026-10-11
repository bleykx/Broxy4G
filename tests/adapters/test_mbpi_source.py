"""Tests de la source d'APN MBPI."""

from __future__ import annotations

from pathlib import Path

from broxy4g.adapters.mbpi_source import (
    DEFAULT_MBPI_PATH,
    MbpiApnSource,
    load_providers,
)

SAMPLE_XML = """\
<?xml version="1.0"?>
<serviceproviders>
  <country code="fr">
    <name>France</name>
    <provider>
      <name>Orange</name>
      <gsm>
        <network-id mcc="208" mnc="01"/>
        <apn value="orange">
          <usage type="internet"/>
        </apn>
        <apn value="orange.acte">
          <usage type="mms"/>
        </apn>
      </gsm>
    </provider>
    <provider>
      <name>NoNetworkId</name>
      <gsm>
        <apn value="generic.apn"/>
      </gsm>
    </provider>
  </country>
</serviceproviders>
"""


def _write_sample(tmp_path: Path) -> str:
    path = tmp_path / "serviceproviders.xml"
    path.write_text(SAMPLE_XML, encoding="utf-8")
    return str(path)


class TestLoadProviders:
    def test_only_internet_apns_when_present(self, tmp_path):
        providers = load_providers(_write_sample(tmp_path))
        orange = [p for p in providers if p.name == "Orange"]
        assert len(orange) == 1
        assert orange[0].mcc == "208"
        assert orange[0].mnc == "01"
        assert [a.apn for a in orange[0].apns] == ["orange"]

    def test_provider_without_network_id_is_skipped(self, tmp_path):
        providers = load_providers(_write_sample(tmp_path))
        assert all(p.name != "NoNetworkId" for p in providers)

    def test_default_file_loads_france(self):
        providers = load_providers()
        assert any(p.mcc == "208" for p in providers)
        assert DEFAULT_MBPI_PATH.exists()


class TestMbpiApnSource:
    def test_resolve_from_sample(self, tmp_path):
        source = MbpiApnSource(_write_sample(tmp_path))
        suggestion = source.resolve("208010000000000", None, None)
        assert suggestion is not None
        assert suggestion.apn == "orange"
        assert suggestion.source == "mbpi"
        assert suggestion.operator == "Orange"

    def test_resolve_unknown_returns_none(self, tmp_path):
        source = MbpiApnSource(_write_sample(tmp_path))
        assert source.resolve(None, "99999", None) is None
