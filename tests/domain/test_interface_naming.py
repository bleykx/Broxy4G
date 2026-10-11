"""Tests du nommage d'interface réseau d'un dongle (domain/interface_naming)."""

from broxy4g.domain.interface_naming import (
    MAX_INTERFACE_NAME_LEN,
    NamedDongle,
    derive_interface_name,
    plan_interface_names,
)


class TestDeriveInterfaceName:
    def test_lowercase_and_compact(self):
        assert derive_interface_name("MyDongle", "imei") == "mydongle"

    def test_strips_accents(self):
        assert derive_interface_name("Élève", "imei") == "eleve"

    def test_replaces_spaces_and_symbols_with_single_dash(self):
        assert derive_interface_name("Ma 4G / box!!", "imei") == "ma-4g-box"

    def test_strips_edge_dashes(self):
        assert derive_interface_name("--Bouygues--", "imei") == "bouygues"

    def test_truncates_to_15_chars(self):
        name = derive_interface_name("abcdefghijklmnopqrstuvwxyz", "imei")
        assert name == "abcdefghijklmno"
        assert len(name) == MAX_INTERFACE_NAME_LEN

    def test_truncation_never_leaves_trailing_dash(self):
        # Le 15e caractère est un '-' : il est retiré après troncature.
        assert derive_interface_name("abcdefghijklmn-pq", "imei") == "abcdefghijklmn"

    def test_empty_label_falls_back_to_dongle(self):
        assert derive_interface_name("", "imei") == "dongle"

    def test_symbol_only_label_falls_back_to_dongle(self):
        assert derive_interface_name("!!! ???", "imei") == "dongle"


class TestPlanInterfaceNames:
    def test_single_dongle_keeps_base_name(self):
        plan = plan_interface_names([NamedDongle("123", "Test")])
        assert plan == {"123": "test"}

    def test_deterministic_regardless_of_input_order(self):
        dongles = [NamedDongle("b-imei", "Test"), NamedDongle("a-imei", "Test")]
        assert plan_interface_names(dongles) == plan_interface_names(reversed(dongles))

    def test_identical_labels_get_identity_suffix(self):
        plan = plan_interface_names(
            [
                NamedDongle("111111111111111", "Test"),
                NamedDongle("222222222222222", "Test"),
            ]
        )
        assert plan["111111111111111"] == "test"
        assert plan["222222222222222"] == "test-2222"

    def test_long_labels_truncated_and_suffix_fits_15(self):
        label = "a" * 40
        plan = plan_interface_names(
            [NamedDongle("1111", label), NamedDongle("2222", label)]
        )
        assert plan["1111"] == "a" * 15
        assert plan["2222"] == "a" * 10 + "-2222"
        assert all(len(name) <= MAX_INTERFACE_NAME_LEN for name in plan.values())

    def test_empty_identity_uses_0000_suffix(self):
        plan = plan_interface_names(
            [NamedDongle("", "Test"), NamedDongle("!!!", "Test")]
        )
        assert plan[""] == "test"
        assert plan["!!!"] == "test-0000"

    def test_all_names_unique_and_within_limit(self):
        dongles = [NamedDongle(str(i), "Même label!!") for i in range(20)]
        plan = plan_interface_names(dongles)
        assert len(plan) == 20
        assert len(set(plan.values())) == 20
        assert all(len(name) <= MAX_INTERFACE_NAME_LEN for name in plan.values())
