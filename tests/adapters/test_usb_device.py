import os

from broxy4g.adapters.usb_device import USB_DEVICES_ROOT, FakeUsbDevice, RealUsbDevice


def _build_sysfs(tmp_path):
    """Façade /sys : ``root/<id>`` → symlink vers ``…/usb1/<id>``."""
    root = tmp_path / "bus" / "usb" / "devices"
    usb_dir = tmp_path / "devices" / "pci0000:00" / "usb1"
    root.mkdir(parents=True)
    usb_dir.mkdir(parents=True)
    for dev_id in ("1-1", "1-1.2"):
        (usb_dir / dev_id).mkdir()
        (usb_dir / dev_id / "authorized").write_text("1\n")
        (root / dev_id).symlink_to(usb_dir / dev_id)
    # Device dont le realpath sort du sous-arbre USB (borne de chemin).
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "authorized").write_text("1\n")
    (root / "9-9").symlink_to(elsewhere)
    # Device USB sans fichier ``authorized``.
    (usb_dir / "2-2").mkdir()
    (root / "2-2").symlink_to(usb_dir / "2-2")
    return root, usb_dir


async def test_resolve_authorized_file_valid(tmp_path):
    root, usb_dir = _build_sysfs(tmp_path)
    usb = RealUsbDevice(str(root))

    path = await usb.resolve_authorized_file("/sys/devices/pci0000:00/usb1/1-1")

    assert path == os.path.realpath(str(usb_dir / "1-1" / "authorized"))


async def test_resolve_multi_segment_device_id(tmp_path):
    """``1-1.2`` ne doit pas matcher le sibling ``1-1`` (frontière stricte)."""
    root, usb_dir = _build_sysfs(tmp_path)
    usb = RealUsbDevice(str(root))

    path = await usb.resolve_authorized_file(
        "/sys/devices/pci0000:00/usb1/1-1.2"
    )

    assert path == os.path.realpath(str(usb_dir / "1-1.2" / "authorized"))


async def test_resolve_rejects_device_outside_usb_subtree(tmp_path):
    """Borne de chemin : realpath hors sous-arbre ``usbN/<id>`` ⇒ refusé."""
    root, _ = _build_sysfs(tmp_path)
    usb = RealUsbDevice(str(root))

    assert await usb.resolve_authorized_file("/sys/devices/pci0000:00/usb1/9-9") is None


async def test_resolve_rejects_missing_authorized_file(tmp_path):
    root, _ = _build_sysfs(tmp_path)
    usb = RealUsbDevice(str(root))

    assert await usb.resolve_authorized_file("/sys/devices/pci0000:00/usb1/2-2") is None


async def test_resolve_rejects_paths_without_usb_fragment(tmp_path):
    root, _ = _build_sysfs(tmp_path)
    usb = RealUsbDevice(str(root))

    assert await usb.resolve_authorized_file("/sys/devices/platform/foo") is None
    assert await usb.resolve_authorized_file(None) is None
    assert await usb.resolve_authorized_file("") is None


async def test_read_write_roundtrip(tmp_path):
    root, usb_dir = _build_sysfs(tmp_path)
    usb = RealUsbDevice(str(root))
    path = os.path.realpath(str(usb_dir / "1-1" / "authorized"))

    assert await usb.read_authorized(path) is True
    await usb.write_authorized(path, False)
    assert await usb.read_authorized(path) is False
    await usb.write_authorized(path, True)
    assert await usb.read_authorized(path) is True


async def test_fake_register_resolve_and_hook():
    fake = FakeUsbDevice()
    events: list[str] = []
    fake.on_reenumeration = lambda: events.append("reeval")

    path = fake.register("/sys/devices/pci0000:00/usb1/1-1")
    assert path == f"{USB_DEVICES_ROOT}/1-1/authorized"
    assert await fake.resolve_authorized_file("/sys/devices/pci0000:00/usb1/1-1") == path
    assert await fake.resolve_authorized_file("/sys/devices/usb1/7-7") is None
    assert await fake.resolve_authorized_file(None) is None

    assert await fake.read_authorized(path) is True
    await fake.write_authorized(path, False)
    assert await fake.read_authorized(path) is False
    assert fake.writes == [(path, False)]
    await fake.write_authorized(path, True)
    assert await fake.read_authorized(path) is True
    assert fake.writes[-1] == (path, True)
    assert events == ["reeval"]  # hook appelé uniquement à la ré-autorisation


async def test_fake_write_unknown_file_is_recorded_without_state():
    fake = FakeUsbDevice()

    await fake.write_authorized(f"{USB_DEVICES_ROOT}/4-4/authorized", True)

    assert fake.writes == [(f"{USB_DEVICES_ROOT}/4-4/authorized", True)]
    assert await fake.read_authorized(f"{USB_DEVICES_ROOT}/4-4/authorized") is False
