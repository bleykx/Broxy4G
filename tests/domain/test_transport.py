from broxy4g.domain.transport import Transport


def test_from_value_none_defaults_to_modem():
    assert Transport.from_value(None) is Transport.MODEM


def test_from_value_unknown_defaults_to_modem():
    assert Transport.from_value("bluetooth") is Transport.MODEM


def test_from_value_adb():
    assert Transport.from_value("adb") is Transport.ADB


def test_from_value_modem():
    assert Transport.from_value("modem") is Transport.MODEM
