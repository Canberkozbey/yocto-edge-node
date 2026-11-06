"""Update, rollback and recovery on the SD card's A/B slots. The tests run
in order; each starts where the one before left the board (conftest.py)."""

import re
import time

import pytest

from slots import (
    boot_status, booted_slot, expect_boot, login, power_cycle, rauc_status, reboot,
)

# The bootloader EEPROM must pick config.txt sections by boot partition
# ([boot_partition=N]), which arrived in its 2025-03-10 release.
EEPROM_MIN_TIMESTAMP = 1740787200  # 2025-03-01 00:00 UTC
ROOT = {"A": "/dev/mmcblk0p4", "B": "/dev/mmcblk0p5"}
OTHER = {"A": "B", "B": "A"}
REMOTE_BUNDLE = "/tmp/update.raucb"


def install(shell, ssh_target, bundle):
    """Copies the bundle to the target and installs it into the other slot."""
    ssh_target().put(bundle, REMOTE_BUNDLE)
    shell.run_check(f"rauc install {REMOTE_BUNDLE}", timeout=900)


def committed(shell, slot):
    status = boot_status(shell)
    return status["default"] == slot and status["booted"] == slot


def test_bootloader_picks_a_command_line_per_slot(sd_card):
    # A big-endian 32-bit value; busybox od prints it as octal bytes after
    # an offset on each line.
    out = sd_card.run_check("od -b /proc/device-tree/chosen/bootloader/build-timestamp")
    timestamp = int.from_bytes(bytes(int(byte, 8) for line in out for byte in line.split()[1:]))
    assert timestamp >= EEPROM_MIN_TIMESTAMP, (
        f"bootloader EEPROM built {time.strftime('%Y-%m-%d', time.gmtime(timestamp))}; "
        "update it (docs/lab.md)")


def test_boots_slot_a_from_the_sd_card(sd_card):
    shell = sd_card
    cmdline = shell.run_check("cat /proc/cmdline")[0].split()
    assert "rauc.slot=A" in cmdline
    assert f"root={ROOT['A']}" in cmdline
    assert committed(shell, "A")
    assert rauc_status(shell)[0] == "A"
    # The commit service ran and succeeded; the data partition is mounted.
    assert shell.run_check("systemctl is-active edgenode-boot-commit") == ["active"]
    mounts = [line.split() for line in shell.run_check("cat /proc/mounts")]
    assert ["/dev/mmcblk0p6", "/data", "ext4"] in [m[:3] for m in mounts]


def test_hardware_watchdog_is_armed(sd_card):
    watchdog = "/sys/class/watchdog/watchdog0"
    assert "BCM2835" in sd_card.run_check(f"cat {watchdog}/identity")[0].upper()
    assert sd_card.run_check(f"cat {watchdog}/state") == ["active"]
    assert int(sd_card.run_check(f"cat {watchdog}/timeout")[0]) <= 15


def test_update_boots_the_other_slot_on_trial_and_commits_it(target, sd_card, ssh_target, bundle):
    shell = sd_card
    install(shell, ssh_target, bundle)
    booted, primary, slots = rauc_status(shell)
    assert (booted, primary) == ("A", "rootfs.1")
    assert slots["rootfs.1"] == "good"
    status = boot_status(shell)
    assert status["default"] == "A"  # not committed before it has booted
    assert status["next"] == "B (trial)"

    assert reboot(target, shell) == "B"
    shell = login(target)
    assert booted_slot(shell) == "B"
    assert f"root={ROOT['B']}" in shell.run_check("cat /proc/cmdline")[0].split()
    # edgenoded came up, so the commit service made B the default.
    assert committed(shell, "B")
    assert rauc_status(shell)[:2] == ("B", "rootfs.1")


def test_power_cut_before_the_trial_boot_keeps_the_running_slot(target, sd_card, ssh_target, bundle):
    shell = login(target)
    install(shell, ssh_target, bundle)
    assert boot_status(shell)["next"] == "A (trial)"

    # The tryboot flag does not survive a power cut: the update is never
    # booted, and the board stays on the slot it had.
    power_cycle(target)
    shell = login(target)
    assert booted_slot(shell) == "B"
    assert committed(shell, "B")


def test_failed_trial_boot_returns_to_the_previous_slot(target, sd_card, ssh_target, bundle):
    shell = login(target)
    install(shell, ssh_target, bundle)
    # Break the installed slot the way a bad release would: its edgenoded
    # exits on start.
    shell.run_check(f"mount {ROOT['A']} /mnt")
    shell.run_check("mkdir -p /mnt/etc/systemd/system/edgenoded.service.d")
    shell.run_check("printf '[Service]\\nExecStart=\\nExecStart=/bin/false\\n' "
                    "> /mnt/etc/systemd/system/edgenoded.service.d/broken.conf")
    shell.run_check("umount /mnt")

    # Trial boot of A; the commit service gives up on edgenoded, marks A bad
    # and reboots, and the firmware starts B again.
    assert reboot(target, shell) == "A"
    assert expect_boot(target) == "B"
    shell = login(target)
    assert committed(shell, "B")
    booted, primary, slots = rauc_status(shell)
    assert (booted, primary) == ("B", "rootfs.1")
    assert slots["rootfs.0"] == "bad"


def test_power_cut_during_installation_keeps_the_running_slot(target, sd_card, ssh_target, bundle):
    shell = login(target)
    ssh_target().put(bundle, REMOTE_BUNDLE)
    shell.run_check(f"(setsid rauc install {REMOTE_BUNDLE} >/tmp/install.log 2>&1 </dev/null &)")

    # Cut the power while RAUC writes the root filesystem into slot A.
    deadline = time.monotonic() + 300
    while True:
        out = " ".join(shell.run("busctl get-property de.pengutronix.rauc / "
                                 "de.pengutronix.rauc.Installer Progress 2>/dev/null")[0])
        if re.search(r"Copying image to rootfs\.0", out):
            break
        if time.monotonic() > deadline:
            pytest.fail(f"installation did not reach the root filesystem: {out}")
        time.sleep(0.5)
    time.sleep(2)
    power_cycle(target)

    shell = login(target)
    assert booted_slot(shell) == "B"
    assert committed(shell, "B")
    # RAUC marked A bad before it started writing.
    assert rauc_status(shell)[2]["rootfs.0"] == "bad"

    # A complete installation afterwards works, and leaves A committed.
    install(shell, ssh_target, bundle)
    assert reboot(target, shell) == "A"
    shell = login(target)
    assert committed(shell, "A")
    assert rauc_status(shell)[2]["rootfs.0"] == "good"


@pytest.mark.parametrize("where", ["signature", "payload"])
def test_tampered_bundle_is_rejected(target, sd_card, ssh_target, bundle, tmp_path, where):
    data = bytearray(bundle.read_bytes())
    # The CMS signature is near the end, before the 8-byte size field; the
    # middle of the file is the squashfs with the images.
    offset = len(data) - 64 if where == "signature" else len(data) // 2
    data[offset] ^= 0xFF
    tampered = tmp_path / "tampered.raucb"
    tampered.write_bytes(data)

    shell = login(target)
    before = boot_status(shell)
    ssh_target().put(tampered, REMOTE_BUNDLE)
    out, _, code = shell.run(f"rauc install {REMOTE_BUNDLE} 2>&1", timeout=900)
    assert code != 0, "\n".join(out)
    if where == "signature":
        assert "signature" in " ".join(out).lower(), "\n".join(out)
    # The running slot stays the default, and nothing is armed for a trial.
    after = boot_status(shell)
    assert after["default"] == before["default"] == after["booted"]
    assert after["next"] == after["default"]


def test_watchdog_resets_a_hung_system(target, sd_card):
    shell = login(target)
    slot = booted_slot(shell)
    # No reboot on panic: only the hardware watchdog can bring it back.
    shell.run_check("echo 0 > /proc/sys/kernel/panic")
    shell.console.write(b"echo c > /proc/sysrq-trigger\n")
    target.deactivate(shell)
    started = time.monotonic()
    assert expect_boot(target) == slot
    # 15 s timeout, plus the firmware's own start.
    assert time.monotonic() - started < 90
    login(target)
