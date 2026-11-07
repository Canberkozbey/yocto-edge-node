"""Update, rollback and recovery on the SD card's A/B slots, the dm-verity
root filesystem, and the node's identity on the data partition, which
updates keep. The tests run in order; each starts where the one before left
the board (conftest.py)."""

import os
import re
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

import helpers
from broker import Broker, Pki, Subscriber
from slots import (
    boot_status, booted_slot, expect_boot, login, power_cycle, rauc_status, reboot,
)

# The bootloader EEPROM must pick config.txt sections by boot partition
# ([boot_partition=N]), which arrived in its 2025-03-10 release.
EEPROM_MIN_TIMESTAMP = 1740787200  # 2025-03-01 00:00 UTC
ROOT = {"A": "/dev/mmcblk0p4", "B": "/dev/mmcblk0p5"}
BOOT = {"A": "/dev/mmcblk0p2", "B": "/dev/mmcblk0p3"}
REMOTE_BUNDLE = "/tmp/update.raucb"


@pytest.fixture(scope="module")
def site(target, tmp_path_factory):
    """A TLS broker on the test host that takes only clients with a
    certificate from its CA, and what a site would provision on a node's
    data partition for it: the site's configuration, and the node's key and
    certificates (docs/adr/0011)."""
    work = tmp_path_factory.mktemp("site")
    listen = helpers.target_option(target, "mqtt_broker_listen")
    reach = helpers.target_option(target, "mqtt_broker_from_target")
    port = int(helpers.target_option(target, "mqtt_broker_port"))
    fleet = Pki(work, "fleet")
    broker = Broker(listen, port, work, tls=fleet.issue("broker", "server", [listen, reach]))
    broker.start()
    node = fleet.issue("node", "client")
    config = work / "edgenoded.conf"
    config.write_text(f"""mqtt_broker = {reach}
mqtt_port = {port}
mqtt_interval = 2
mqtt_ca = /data/edgenode/mqtt/ca.cert.pem
mqtt_cert = /data/edgenode/mqtt/node.cert.pem
mqtt_key = /data/edgenode/mqtt/node.key.pem
""")
    files = {config: "edgenoded.conf", node.ca: "mqtt/ca.cert.pem",
             node.cert: "mqtt/node.cert.pem", node.key: "mqtt/node.key.pem"}
    yield SimpleNamespace(broker=broker, files=files,
                          dashboard=fleet.issue("dashboard", "client"))
    broker.stop()


def comes_online(shell, site):
    """A dashboard sees the node's edgenoded online at the site's broker."""
    prefix = f"edgenode/{shell.run_check('hostname')[0]}"
    dashboard = Subscriber(site.broker, f"{prefix}/#", tls=site.dashboard)
    try:
        return dashboard.wait_for(f"{prefix}/status", lambda p: p == "online", timeout=90)
    finally:
        dashboard.close()


def connected_over_tls(shell, timeout=90):
    """This boot's edgenoded logged its connection to the broker over TLS:
    the broker takes only clients with a certificate from its CA."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if shell.run("journalctl -b -u edgenoded | "
                     "grep -q 'Connected to MQTT broker .* over TLS'")[2] == 0:
            return True
        time.sleep(2)
    return False


def host_keys(shell):
    return sorted(shell.run_check("cat /run/ssh/*.pub"))


def ls_mode(shell, path):
    """(mode string, owner, group) as ls -ld shows them."""
    fields = shell.run_check(f"ls -ld {path}")[0].split()
    return fields[0], fields[2], fields[3]


def install(shell, ssh_target, bundle):
    """Copies the bundle to the target and installs it into the other slot."""
    ssh_target().put(bundle, REMOTE_BUNDLE)
    shell.run_check(f"rauc install {REMOTE_BUNDLE}", timeout=900)


def committed(shell, slot):
    status = boot_status(shell)
    return status["default"] == slot and status["booted"] == slot


def verity_root_over(shell):
    """The partition under the verity device the root filesystem is on, or
    None when / is not on it."""
    root = next(line.split() for line in shell.run_check("cat /proc/self/mountinfo")
                if line.split()[4] == "/")
    if shell.run("test -e /sys/block/dm-0")[2] != 0:
        return None
    if root[2] != shell.run_check("cat /sys/block/dm-0/dev")[0]:
        return None
    return "/dev/" + shell.run_check("ls /sys/block/dm-0/slaves")[0]


def fat_tool(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True,
                          env={**os.environ, "MTOOLS_SKIP_CHECK": "1"}, **kwargs).stdout


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
    assert verity_root_over(shell) == ROOT["A"]
    assert committed(shell, "A")
    assert rauc_status(shell)[0] == "A"
    # The commit service ran and succeeded; the data partition is mounted.
    assert shell.run_check("systemctl is-active edgenode-boot-commit") == ["active"]
    mounts = [line.split() for line in shell.run_check("cat /proc/mounts")]
    assert ["/dev/mmcblk0p6", "/data", "ext4"] in [m[:3] for m in mounts]


def test_root_filesystem_is_verified_and_read_only(sd_card):
    shell = sd_card
    # / is the dm-verity device, named as the command line names it.
    assert shell.run_check("cat /sys/block/dm-0/dm/name") == ["vroot"]
    root = next(line.split() for line in shell.run_check("cat /proc/self/mountinfo")
                if line.split()[4] == "/")
    assert "ro" in root[5].split(","), root
    # Nothing on it can be changed, not even by root.
    out, _, code = shell.run("touch /usr/lib/edgenode-write-test 2>&1")
    assert code != 0 and "Read-only" in " ".join(out), out
    assert shell.run("mount -o remount,rw / 2>&1")[2] != 0
    # The node's service runs as before; RAUC's state is on /data.
    assert shell.run_check("systemctl is-active edgenoded") == ["active"]
    assert shell.run("test -d /data/rauc")[2] == 0


def test_hardware_watchdog_is_armed(sd_card):
    watchdog = "/sys/class/watchdog/watchdog0"
    assert "BCM2835" in sd_card.run_check(f"cat {watchdog}/identity")[0].upper()
    assert sd_card.run_check(f"cat {watchdog}/state") == ["active"]
    assert int(sd_card.run_check(f"cat {watchdog}/timeout")[0]) <= 15


def test_node_identity_lives_on_the_data_partition(sd_card, ssh_target, site):
    shell = sd_card
    # The SSH host keys: made on the card's first boot, on /data.
    mounts = [line.split() for line in shell.run_check("cat /proc/mounts")]
    assert ["/dev/mmcblk0p6", "/run/ssh"] in [m[:2] for m in mounts]
    site.host_keys = host_keys(shell)
    assert site.host_keys

    # Provisioned as a site might: the key readable by root only. The site
    # unit gives the daemon's group read access, and nobody else.
    ssh = ssh_target()
    ssh.run_check("mkdir -p /data/edgenode/mqtt")
    for local, remote in site.files.items():
        ssh.put(local, f"/data/edgenode/{remote}")
    ssh.run_check("chmod 0600 /data/edgenode/mqtt/node.key.pem && "
                  "systemctl restart edgenoded-site.service edgenoded.service")
    assert comes_online(shell, site), "\n".join(shell.run("journalctl -b -u edgenoded")[0])
    assert connected_over_tls(shell, timeout=10)
    assert ls_mode(shell, "/data/edgenode") == ("drwxr-x---", "root", "edgenode-identity")
    assert ls_mode(shell, "/data/edgenode/mqtt/node.key.pem") == \
        ("-rw-r-----", "root", "edgenode-identity")


def test_update_boots_the_other_slot_on_trial_and_commits_it(target, sd_card, ssh_target, bundle,
                                                             site):
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
    assert verity_root_over(shell) == ROOT["B"]
    # edgenoded came up, so the commit service made B the default.
    assert committed(shell, "B")
    assert rauc_status(shell)[:2] == ("B", "rootfs.1")
    # The node is still itself: the same SSH host keys, and the same MQTT
    # identity and site configuration, all from /data.
    assert host_keys(shell) == site.host_keys
    assert connected_over_tls(shell), "\n".join(shell.run("journalctl -b -u edgenoded")[0])


def test_power_cut_before_the_trial_boot_keeps_the_running_slot(target, sd_card, ssh_target, bundle):
    shell = login(target)
    install(shell, ssh_target, bundle)
    assert boot_status(shell)["next"] == "A (trial)"

    # The tryboot flag does not survive a power cut: the update is never
    # booted, and the board stays on the slot it had. The commit service
    # records the abandoned trial.
    power_cycle(target)
    shell = login(target)
    assert booted_slot(shell) == "B"
    assert committed(shell, "B")
    assert rauc_status(shell)[2]["rootfs.0"] == "bad"


def test_failed_trial_boot_returns_to_the_previous_slot(target, sd_card, ssh_target, bundle,
                                                        deploy_dir, tmp_path):
    shell = login(target)
    install(shell, ssh_target, bundle)
    # Make the installed slot behave like a bad release: its edgenoded does
    # not start. The root filesystem cannot be changed without failing
    # verification, so the slot's command line masks the service instead:
    # the same boot slot image as installed, one argument more.
    boot = tmp_path / "bootfs.vfat"
    shutil.copyfile(deploy_dir / "edgenode-image-dev-raspberrypi5.rootfs.bootfs.vfat", boot)
    cmdline = fat_tool("mtype", "-i", str(boot), "::cmdline-A.txt").decode().strip()
    (tmp_path / "cmdline-A.txt").write_text(cmdline + " systemd.mask=edgenoded.service\n")
    fat_tool("mcopy", "-o", "-i", str(boot), str(tmp_path / "cmdline-A.txt"), "::cmdline-A.txt")
    ssh = ssh_target()
    ssh.put(boot, BOOT["A"])
    ssh.run_check("sync")

    # Trial boot of A; the commit service gives up on edgenoded, marks A bad
    # and reboots, and the firmware starts B again.
    assert reboot(target, shell) == "A"
    assert expect_boot(target) == "B"
    shell = login(target)
    assert committed(shell, "B")
    booted, primary, slots = rauc_status(shell)
    assert (booted, primary) == ("B", "rootfs.1")
    assert slots["rootfs.0"] == "bad"


def test_tampered_root_filesystem_is_not_booted(target, sd_card, ssh_target, bundle):
    shell = login(target)
    install(shell, ssh_target, bundle)
    # Change one block of the installed root filesystem, as an attacker with
    # the card or a failing card would: the first, with the ext4 superblock.
    shell.run_check(f"dd if=/dev/zero of={ROOT['A']} bs=4096 count=1 2>/dev/null && sync")

    # The trial boot's kernel cannot mount the root filesystem (the block
    # fails verification), panics and reboots; the firmware starts B again,
    # and the commit service records the failed trial.
    assert reboot(target, shell) == "A"
    assert expect_boot(target) == "B"
    shell = login(target)
    assert committed(shell, "B")
    assert rauc_status(shell)[2]["rootfs.0"] == "bad"


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
