from enum import StrEnum


class Transport(StrEnum):
    """Type de transport d'un appareil (dongle modem ou téléphone ADB)."""

    MODEM = "modem"
    ADB = "adb"

    @classmethod
    def from_value(cls, value: str | None) -> "Transport":
        """Construit un Transport depuis une valeur brute. None/inconnu -> MODEM."""
        if value is None:
            return cls.MODEM
        for member in cls:
            if member.value == value:
                return member
        return cls.MODEM
