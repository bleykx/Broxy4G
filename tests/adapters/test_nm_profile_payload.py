"""Tests purs de ``nm_profile_payload`` : variant GVariant mesuré + schéma de vérification."""

import pytest

from broxy4g.adapters.nm_profile_payload import (
    VERIFY_FIELDS_IFACE,
    VERIFY_FIELDS_MAC,
    build_profile_variant,
    check_profile_values,
    gquote,
    normalize_identity,
    parse_mac_bytes,
)

NAME = "broxy4g-phone-X"
IFACE = "enx02360c7c147a"
MAC = "02:36:0c:7c:14:7a"
METRIC = 21000

# (a) Forme littérale mesurée : acceptée par le démon NM 1.46, MAC relue,
# liaison par MAC (connection.interface-name vide).
EXPECTED_MAC_VARIANT = (
    "{'connection': {'id': <'broxy4g-phone-X'>, 'type': <'802-3-ethernet'>, "
    "'autoconnect': <true>}, "
    "'802-3-ethernet': {'mac-address': "
    "<[byte 0x02, byte 0x36, byte 0x0c, byte 0x7c, byte 0x14, byte 0x7a]>}, "
    "'ipv4': {'method': <'auto'>, 'never-default': <true>, 'route-metric': <21000>}, "
    "'ipv6': {'method': <'disabled'>}}"
)

# (c) Repli inchangé : liaison par connection.interface-name, PAS de setting
# 802-3-ethernet.
EXPECTED_IFACE_VARIANT = (
    "{'connection': {'id': <'broxy4g-phone-X'>, 'type': <'802-3-ethernet'>, "
    "'interface-name': <'enx02360c7c147a'>, 'autoconnect': <true>}, "
    "'ipv4': {'method': <'auto'>, 'never-default': <true>, 'route-metric': <21000>}, "
    "'ipv6': {'method': <'disabled'>}}"
)


def test_mac_variant_exact_measured_form():
    """(a) Le variant MAC est la forme mesurée exacte, chaîne pour chaîne."""
    assert build_profile_variant(NAME, IFACE, MAC, METRIC).variant == EXPECTED_MAC_VARIANT


def test_mac_variant_normalizes_uppercase_input():
    binding = build_profile_variant(NAME, IFACE, MAC.upper(), METRIC)
    assert binding.variant == EXPECTED_MAC_VARIANT  # hex minuscule dans le payload
    assert binding.expected_identity == MAC.lower()
    assert binding.verify_fields == VERIFY_FIELDS_MAC


def test_mac_mode_connection_block_without_identity_keys():
    """(b)+(f) En mode MAC, le bloc ``connection`` ne porte ni ``mac-address``
    (rejeté : ``unknown property``) ni ``interface-name`` ; ``mac-address``
    n'existe que dans le setting ``802-3-ethernet``."""
    variant = build_profile_variant(NAME, IFACE, MAC, METRIC).variant
    connection_block = variant.split(", '802-3-ethernet':")[0]
    assert "mac-address" not in connection_block
    assert "interface-name" not in connection_block
    assert "'mac-address': <[byte" in variant  # bien présent dans le setting dédié


def test_mac_none_fallback_unchanged_without_ethernet_setting():
    """(c) Repli ``mac=None`` : ``interface-name`` dans ``connection``, aucun
    setting ``802-3-ethernet`` (hors la valeur de ``connection.type``)."""
    binding = build_profile_variant(NAME, IFACE, None, METRIC)
    assert binding.variant == EXPECTED_IFACE_VARIANT
    assert "'802-3-ethernet': {" not in binding.variant
    assert binding.verify_fields == VERIFY_FIELDS_IFACE
    assert binding.expected_identity == IFACE


def test_invalid_mac_falls_back_to_interface_name():
    """(d) ``mac`` non None mais ≠ 6 octets hex → repli ``interface-name``."""
    for bad in (
        "zz:36:0c:7c:14:7a",  # hex invalide
        "02:36:0c:7c:14",  # trop court
        "02:36:0c:7c:14:7a:ff",  # trop long
        "0236:0c:7c:14:7a",  # groupe à 4 chiffres
        "02-36-0c-7c-14-7a",  # mauvais séparateur
        " 02:36:0c:7c:14:7a",  # espace
        "",
    ):
        binding = build_profile_variant(NAME, IFACE, bad, METRIC)
        assert binding.variant == EXPECTED_IFACE_VARIANT, bad
        assert binding.expected_identity == IFACE, bad


def test_parse_mac_bytes():
    assert parse_mac_bytes(MAC) == [0x02, 0x36, 0x0C, 0x7C, 0x14, 0x7A]
    assert parse_mac_bytes(MAC.upper()) == [0x02, 0x36, 0x0C, 0x7C, 0x14, 0x7A]
    assert parse_mac_bytes("00:00:00:00:00:00") == [0, 0, 0, 0, 0, 0]  # identité légitime
    assert parse_mac_bytes("zz:zz:zz:zz:zz:zz") is None
    assert parse_mac_bytes("") is None
    assert parse_mac_bytes("02:36:0c:7c:14:7a:") is None
    assert parse_mac_bytes("g2:36:0c:7c:14:7a") is None


def test_gquote_escapes_single_quotes_and_backslashes():
    assert gquote("plain") == "plain"
    assert gquote("it's") == "it\\'s"
    assert gquote("a\\b") == "a\\\\b"


def test_normalize_identity_strips_backslashes_and_lowercases():
    """Sortie mesurée ``02\\:36\\:0C\\:7C\\:14\\:7A`` → identique au MAC brut."""
    assert normalize_identity("02\\:36\\:0C\\:7C\\:14\\:7A") == "02:36:0c:7c:14:7a"
    assert normalize_identity("02:36:0c:7c:14:7a") == "02:36:0c:7c:14:7a"
    assert normalize_identity("ENX02360C7C147A") == "enx02360c7c147a"


def _expected_values(metric: int = METRIC, identity: str = MAC) -> list[str]:
    return ["auto", "yes", "disabled", str(metric), "yes", identity]


def test_check_profile_values_conforming():
    assert check_profile_values(_expected_values(), _expected_values()) == (True, False)


def test_check_profile_values_escaped_uppercase_mac_conforming():
    """(e) Relecture mesurée ``02\\:36\\:0C\\:7C\\:14\\:7A`` → conforme."""
    values = ["auto", "yes", "disabled", "21000", "yes", "02\\:36\\:0C\\:7C\\:14\\:7A"]
    assert check_profile_values(values, _expected_values()) == (True, False)


def test_check_profile_values_wrong_mac_is_positive_wrong_identity():
    """(e) Autre MAC → non conforme ET identité positive et fausse (l'appelant
    supprime le profil écrit)."""
    values = ["auto", "yes", "disabled", "21000", "yes", "02:36:0c:7c:99:99"]
    assert check_profile_values(values, _expected_values()) == (False, True)
    values_escaped = ["auto", "yes", "disabled", "21000", "yes", "02\\:36\\:0C\\:7C\\:99\\:99"]
    assert check_profile_values(values_escaped, _expected_values()) == (False, True)


def test_check_profile_values_empty_identity_is_nonconclusive():
    values = ["auto", "yes", "disabled", "21000", "yes", ""]
    assert check_profile_values(values, _expected_values()) == (False, False)


@pytest.mark.parametrize("field, bad", [(0, "manual"), (1, "no"), (2, "auto"), (3, "1"), (4, "no")])
def test_check_profile_values_wrong_non_identity_field_is_nonconclusive(field: int, bad: str):
    """(e) Champ politique faux → non conforme mais **pas** identité positive et
    fausse → pas de suppression par l'appelant."""
    values = _expected_values()
    values[field] = bad
    assert check_profile_values(values, _expected_values()) == (False, False)


def test_check_profile_values_length_mismatch_is_nonconclusive():
    assert check_profile_values(_expected_values()[:5], _expected_values()) == (False, False)
    assert check_profile_values(_expected_values() + ["x"], _expected_values()) == (False, False)


def test_mac_variant_parses_as_gvariant_ay():
    """Si ``gi`` est importable dans le venv : le variant est parsé par
    ``GLib.Variant.parse`` et ``mac-address`` est bien un ``ay`` de 6 octets.
    Skip propre sinon (vérifié : ``gi`` absent du venv)."""
    gi = pytest.importorskip("gi")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib  # noqa: PLC0415

    variant = build_profile_variant(NAME, IFACE, MAC, METRIC).variant
    parsed = GLib.Variant.parse("a{sv}", variant)
    top = parsed.get_dict()
    connection = top.lookup_value(GLib.Variant("s", "connection"), "a{s*v}")
    ethernet = top.lookup_value(GLib.Variant("s", "802-3-ethernet"), "a{s*v}")
    assert not connection.get_dict().contains(GLib.Variant("s", "mac-address"))
    assert not connection.get_dict().contains(GLib.Variant("s", "interface-name"))
    mac = ethernet.get_dict().lookup_value(GLib.Variant("s", "mac-address"), "ay")
    assert bytes(mac.get_data()) == bytes.fromhex("02360c7c147a")
