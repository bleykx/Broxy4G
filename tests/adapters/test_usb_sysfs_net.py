"""Tests usb_sysfs_net : arborescence sysfs factice sous ``tmp_path``."""

from broxy4g.adapters.usb_sysfs_net import (
    UsbNetInterface,
    find_net_interfaces_for_usb_serial,
    find_usb_device_dir,
)

SERIAL = "R58N729DZMV"
IFACE = "enxDEADBEEF"
MAC = "02:36:0c:7c:14:7a"


def make_device_dir(tmp_path, serial=SERIAL, with_address=True, with_operstate=True, operstate="up"):
    device = tmp_path / "1-2"
    device.mkdir()
    (device / "serial").write_text(serial + "\n")
    iface = device / "1-2:1.0" / "net" / IFACE
    iface.mkdir(parents=True)
    if with_address:
        (iface / "address").write_text(MAC + "\n")
    if with_operstate:
        (iface / "operstate").write_text(operstate + "\n")
    return device


def test_find_usb_device_dir_matches_serial_file(tmp_path):
    device = make_device_dir(tmp_path)
    assert find_usb_device_dir(SERIAL, str(tmp_path)) == str(device)


def test_find_usb_device_dir_ignores_other_serials_and_missing_root(tmp_path):
    make_device_dir(tmp_path)
    assert find_usb_device_dir("OTHER-SERIAL", str(tmp_path)) is None
    assert find_usb_device_dir(SERIAL, str(tmp_path / "n'existe-pas")) is None


def test_find_net_interfaces_returns_up_interface(tmp_path):
    make_device_dir(tmp_path)
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    assert interfaces == [UsbNetInterface(name=IFACE, mac=MAC, operstate="up")]


def test_find_net_interfaces_non_matching_serial_returns_empty(tmp_path):
    make_device_dir(tmp_path)
    assert find_net_interfaces_for_usb_serial("OTHER-SERIAL", str(tmp_path)) == []


def test_find_net_interfaces_tolerates_missing_address_and_operstate(tmp_path):
    make_device_dir(tmp_path, with_address=False, with_operstate=False)
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    assert interfaces == [UsbNetInterface(name=IFACE, mac=None, operstate=None)]


def test_find_net_interfaces_sorts_up_first(tmp_path):
    device_dir = make_device_dir(tmp_path)
    down_iface = device_dir / "1-2:1.0" / "net" / "enxCAFEBABE"
    down_iface.mkdir(parents=True)
    (down_iface / "operstate").write_text("down\n")
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    assert [iface.name for iface in interfaces] == [IFACE, "enxCAFEBABE"]
    assert interfaces[0].operstate == "up"


def make_foreign_tree(root, iface_name="dongle-1"):
    """Arborescence « étrangère » (autre device du même pilote / du bus)."""
    foreign_iface = root / "1-9:1.1" / "net" / iface_name
    foreign_iface.mkdir(parents=True)
    (foreign_iface / "operstate").write_text("unknown\n")
    return foreign_iface


def test_symlinked_driver_does_not_leak_foreign_netdevs(tmp_path):
    """Non-régression du défaut mesuré sur le Pi : le lien ``driver`` pointe
    vers le répertoire du pilote, qui liste les netdevs d'*autres* dongles."""
    device = make_device_dir(tmp_path)
    make_foreign_tree(tmp_path / "driver-tree")
    (device / "driver").symlink_to(tmp_path / "driver-tree")
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    assert [iface.name for iface in interfaces] == [IFACE]


def test_symlinked_subsystem_does_not_leak_foreign_netdevs(tmp_path):
    """Le lien ``subsystem`` pointe vers ``/sys/bus/usb`` : tout le bus."""
    device = make_device_dir(tmp_path)
    make_foreign_tree(tmp_path / "bus-tree")
    (device / "subsystem").symlink_to(tmp_path / "bus-tree")
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    assert [iface.name for iface in interfaces] == [IFACE]


def test_symlinks_back_to_self_do_not_duplicate_interfaces(tmp_path):
    """Comme ``port/device`` sur le Pi : les liens renvoyant vers l'arborescence
    du device lui-même ne doivent pas dupliquer les interfaces."""
    device = make_device_dir(tmp_path)
    (device / "driver").symlink_to(device)
    (device / "subsystem").symlink_to(device)
    port = device / "port"
    port.mkdir()
    (port / "device").symlink_to(device)
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    names = [iface.name for iface in interfaces]
    assert names == [IFACE]
    assert len(names) == len(set(names))


def test_nested_real_device_is_still_found_and_sorted(tmp_path):
    """Le cas légitime imbriqué (vrais répertoires) continue d'être trouvé,
    avec le tri « up d'abord puis nom » inchangé."""
    device = make_device_dir(tmp_path)  # IFACE en "up"
    nested_up = device / "parent" / "1-2:1.1" / "net" / "enxNESTED"
    nested_up.mkdir(parents=True)
    (nested_up / "operstate").write_text("up\n")
    nested_down = device / "parent" / "child" / "net" / "enxNESTDOWN"
    nested_down.mkdir(parents=True)
    (nested_down / "operstate").write_text("down\n")
    interfaces = find_net_interfaces_for_usb_serial(SERIAL, str(tmp_path))
    assert [iface.name for iface in interfaces] == [IFACE, "enxNESTED", "enxNESTDOWN"]
