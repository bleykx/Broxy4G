"""Tests du domaine de résolution d'APN."""

from __future__ import annotations

import pytest

from broxy4g.domain.apn_resolution import (
    ApnEntry,
    NetworkProvider,
    candidate_network_ids,
    resolve_apn,
)

FREE = NetworkProvider("Free Mobile", "208", "15", (ApnEntry("free"),))
ORANGE = NetworkProvider(
    "Orange",
    "208",
    "01",
    (ApnEntry("orange.fr"), ApnEntry("internet-entreprise"), ApnEntry("orange")),
)
BOUYGUES = NetworkProvider(
    "Bouygues Telecom",
    "208",
    "20",
    (ApnEntry("a2bouygtel.com"), ApnEntry("ebouygtel.com"), ApnEntry("mmsbouygtel.com")),
)
LEBARA = NetworkProvider("Lebara", "208", "10", (ApnEntry("fr.lebara.mobi"),))
SFR = NetworkProvider(
    "SFR",
    "208",
    "10",
    (ApnEntry("websfr"), ApnEntry("sl2sfr"), ApnEntry("internetpro")),
)
PROVIDERS = [BOUYGUES, FREE, LEBARA, SFR, ORANGE]


class TestCandidateNetworkIds:
    def test_two_digit_mnc(self):
        assert candidate_network_ids(None, "20815") == [("208", "15")]

    def test_three_digit_mnc(self):
        assert candidate_network_ids(None, "208150") == [("208", "15"), ("208", "150")]

    def test_operator_id_before_imsi(self):
        result = candidate_network_ids("208150043462938", "20820")
        assert result[0] == ("208", "20")

    def test_empty_inputs(self):
        assert candidate_network_ids(None, None) == []
        assert candidate_network_ids("", "abc") == []


class TestResolveApn:
    def test_free_by_imsi(self):
        result = resolve_apn("208150043462938", None, None, PROVIDERS)
        assert result is not None
        assert result.apn == "free"
        assert result.operator == "Free Mobile"
        assert result.source == "mbpi"

    def test_free_by_operator_id(self):
        result = resolve_apn(None, "20815", None, PROVIDERS)
        assert result is not None
        assert result.apn == "free"

    def test_orange_by_id_with_name(self):
        result = resolve_apn(None, "20801", "Orange", PROVIDERS)
        assert result is not None
        assert result.operator == "Orange"
        assert result.apn == "orange"

    def test_bouygues_prefers_consumer_apn(self):
        result = resolve_apn(None, "20820", "Bouygues Telecom", PROVIDERS)
        assert result is not None
        assert result.apn == "mmsbouygtel.com"

    def test_sfr_disambiguated_by_name(self):
        # Lebara est enregistré avant SFR pour (208, 10) : le nom doit primer.
        result = resolve_apn(None, "20810", "SFR", PROVIDERS)
        assert result is not None
        assert result.operator == "SFR"
        assert result.apn == "websfr"

    def test_shared_mnc_without_name_takes_first(self):
        result = resolve_apn(None, "20810", None, PROVIDERS)
        assert result is not None
        assert result.operator == "Lebara"

    def test_name_only_fallback(self):
        result = resolve_apn(None, None, "Free", PROVIDERS)
        assert result is not None
        assert result.apn == "free"

    def test_unknown_identity(self):
        assert resolve_apn(None, None, "Unknown", PROVIDERS) is None
        assert resolve_apn("999990000000000", None, None, PROVIDERS) is None

    def test_provider_without_apn(self):
        providers = [NetworkProvider("Empty", "208", "99", ())]
        assert resolve_apn(None, "20899", None, providers) is None

    def test_no_providers(self):
        assert resolve_apn("208150043462938", None, None, []) is None


@pytest.mark.parametrize("imsi", ["208150043462938", None])
def test_resolve_is_deterministic(imsi):
    first = resolve_apn(imsi, "20815", "Free Mobile", PROVIDERS)
    second = resolve_apn(imsi, "20815", "Free Mobile", PROVIDERS)
    assert first == second
