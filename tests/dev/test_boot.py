"""The image boots into the expected, healthy system."""

import pytest

import helpers


def test_boot_completes_without_failed_units(shell):
    state = shell.run("systemctl is-system-running")[0]
    failed = shell.run("systemctl --failed --no-legend --plain")[0]
    assert state == ["running"], f"system is {state}, failed units: {failed}"


def test_network_boot_mounts_the_root_read_only(shell):
    # As from the SD card: the image runs on a read-only root
    # (docs/adr/0009), and systemd-remount-fs then has nothing to change.
    # Remounting an NFS root read-only during the boot failed once in CI
    # (tools/netboot.py).
    cmdline = shell.run_check("cat /proc/cmdline")[0].split()
    if not any(arg.startswith("nfsroot=") for arg in cmdline):
        pytest.skip("not a network boot")
    assert "ro" in cmdline and "rw" not in cmdline, cmdline


def test_ssh_logins_are_in_the_journal(shell, ssh_address):
    # sshd logs through syslog (/dev/log), which only journald may read: the
    # journal is the node's only log (docs/adr/0003).
    def logins():
        out = shell.run("journalctl -b --no-pager -o cat | grep -c 'Accepted .* for root from'")[0]
        return int(out[0]) if out else 0

    before = logins()
    assert helpers.ssh_accepts_empty_password(*ssh_address, user="root")
    assert helpers.wait_until(lambda: logins() > before, timeout=10)


def test_runs_edgenode_distro(shell):
    os_release = dict(
        line.split("=", 1) for line in shell.run_check("cat /etc/os-release") if "=" in line
    )
    assert os_release["ID"] == "edgenode"


def test_init_is_systemd(shell):
    assert shell.run_check("cat /proc/1/comm") == ["systemd"]


def test_only_wired_network(shell):
    # The node is wired only (docs/adr/0003): on the Raspberry Pi 5 the Wi-Fi
    # and Bluetooth radios are disabled in the device tree, so the kernel
    # creates no interfaces for them.
    interfaces = shell.run_check("ls -1 /sys/class/net")
    assert not [name for name in interfaces if name.startswith("wlan")], interfaces
    # The class directory may exist without devices, or not at all.
    bluetooth = shell.run_check("ls -1 /sys/class/bluetooth 2>/dev/null || true")
    assert not bluetooth, bluetooth


def test_sd_card_slot_units_stay_out_of_other_boots(shell):
    # The data partition's mount, what lives on it and the slot commit are
    # added only to boots from an A/B slot (edgenode-boot-generator,
    # Raspberry Pi 5). Any other boot, such as network boot in the lab, must
    # not even wait for the SD card's partitions.
    if shell.run("systemctl cat data.mount >/dev/null 2>&1")[2] != 0:
        pytest.skip("no A/B update system in this image")
    if "rauc.slot=" in shell.run_check("cat /proc/cmdline")[0]:
        pytest.skip("booted from an A/B slot")
    for unit in ("data.mount", "edgenode-data.service", "run-ssh.mount",
                 "edgenode-boot-commit.service"):
        never_started = shell.run_check(
            f"systemctl show --property=ConditionTimestampMonotonic --value {unit}")
        assert never_started == ["0"], unit
    # edgenoded always asks for its site directory, which skips itself.
    assert shell.run_check(
        "systemctl show --property=ConditionResult --value edgenoded-site.service") == ["no"]
