"""Driving the Raspberry Pi 5 through boots of its A/B slots: power, the
serial console, and the slot state as edgenode-boot and RAUC report it."""

import json
import re

DEVICE = "/dev/mmcblk0"
BOOT_TIMEOUT = 300


def login(target):
    """Logs in on the serial console once the target boots, and waits for
    the boot to finish, the commit of an A/B slot included."""
    shell = target.get_driver("ShellDriver", activate=False)
    shell.login_timeout = BOOT_TIMEOUT
    target.activate(shell)
    shell.run("dmesg -n 1")  # keep kernel messages off the console
    shell.run("systemctl is-system-running --wait", timeout=BOOT_TIMEOUT)
    return shell


def power_cycle(target):
    """Cuts the power and switches it on again: the firmware's tryboot flag
    does not survive this."""
    target.deactivate(target.get_driver("ShellDriver", activate=False))
    target.get_driver("PowerProtocol").cycle()


def expect_boot(target):
    """Waits for the next boot on the serial console; returns the slot the
    kernel command line names, or None."""
    console = target.get_driver("ConsoleProtocol")
    console.expect("Booting Linux on physical CPU", timeout=BOOT_TIMEOUT)
    # Up to the line's end: expect() matches against whatever has arrived so
    # far, and without the end a partly received line would match.
    _, _, match, _ = console.expect(r"Kernel command line: ([^\r\n]*)[\r\n]", timeout=60)
    slot = re.search(r"rauc\.slot=([AB])", match.group(1).decode())
    return slot.group(1) if slot else None


def reboot(target, shell):
    """Reboots through systemd, which keeps the tryboot flag; returns the
    slot that boots."""
    shell.run("systemctl --no-block reboot")
    target.deactivate(shell)
    return expect_boot(target)


def boot_status(shell):
    """edgenode-boot's view: {"default": "A", "booted": "A", "next": "A",
    "slot A": "good", "slot B": "good"}; a slot on trial reads "B (trial)"."""
    status = {}
    for line in shell.run_check("edgenode-boot status"):
        key, sep, value = line.partition(":")
        if sep:
            status[key.strip()] = value.strip()
    return status


def rauc_status(shell):
    """RAUC's view: (booted slot, primary slot class, {slot: boot status})."""
    out = shell.run_check("rauc status --output-format=json 2>/dev/null")
    status = json.loads(next(line for line in out if line.startswith("{")))
    slots = {name: info.get("boot_status")
             for entry in status["slots"] for name, info in entry.items()
             if info.get("class") == "rootfs"}
    return status["booted"], status["boot_primary"], slots


def booted_slot(shell):
    cmdline = shell.run_check("cat /proc/cmdline")[0].split()
    slots = [arg.split("=", 1)[1] for arg in cmdline if arg.startswith("rauc.slot=")]
    return slots[0] if slots else None
