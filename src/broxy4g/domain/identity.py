from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityMaterial:
    imei: str | None = None
    usb_serial: str | None = None
    iccid: str | None = None

    @property
    def primary_key(self) -> str:
        """Clé d'identité : IMEI > usb_serial > iccid. Lève ValueError si aucun."""
        if self.imei:
            return self.imei
        if self.usb_serial:
            return self.usb_serial
        if self.iccid:
            return self.iccid
        raise ValueError("Aucun identifiant matériel disponible pour construire la clé primaire")

    @property
    def is_complete(self) -> bool:
        """True si au moins un identifiant est présent."""
        return bool(self.imei or self.usb_serial or self.iccid)

    def matches(self, other: 'IdentityMaterial') -> bool:
        """Deux identités correspondent si leur clé primaire est identique."""
        return self.primary_key == other.primary_key