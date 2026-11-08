#!/usr/bin/env python3
"""Serve a built Raspberry Pi 5 image to the lab's Pi over the network.

The Pi's bootloader fetches its boot files over TFTP, from a directory named
after its MAC address (bootloader setting TFTP_PREFIX=2), and the kernel
mounts the root filesystem over NFS. This script fills both from the build
output; docs/lab.md describes the host setup it relies on.

    sudo tools/netboot.py deploy edgenode-image-dev --images "$LG_IMAGES"
    sudo tools/netboot.py remove

Run from a checkout, it reads the repository's lab/lab.toml. CI runs a
root-owned installed copy instead (/usr/local/sbin/edgenode-netboot, allowed
by a sudo rule), which reads only the root-owned /etc/edgenode/lab.toml
(docs/lab.md, docs/adr/0005).

The boot files are copied out of the image's own boot partition (slot A's),
so a network boot uses exactly what the SD card would. Only cmdline.txt
changes, to mount the root filesystem over NFS. After `remove` the Pi boots from its SD card
again. Deploy while the Pi is off or about to be power-cycled: replacing the
root filesystem under a running system breaks it.
"""

import argparse
import bz2
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_CONFIG = Path(__file__).resolve().parent.parent / "lab" / "lab.toml"
SYSTEM_CONFIG = Path("/etc/edgenode/lab.toml")
MACHINE = "raspberrypi5"
IMAGES = ("edgenode-image", "edgenode-image-dev")
BOOT_PARTITION_NAME = "bootA"


def mac_directory(mac):
    """The TFTP directory name the bootloader uses with TFTP_PREFIX=2."""
    return mac.lower().replace(":", "-")


def netboot_cmdline(sd_cmdline, server, nfs_path, address, network):
    """Kernel command line for an NFS root, made from the SD card's.

    Drops the SD root arguments and keeps the rest (console, interface
    names). The kernel configures eth0 itself, with the Pi's reserved address
    set statically: the router does not always give the kernel's DHCP client
    the reserved address, and the NFS export and the tests expect that one.
    Configuring the interface on the command line also keeps
    systemd-networkd away from it: oe-core's wired.network does not match
    when the command line has nfsroot or ip.
    """
    sd_root_args = ("root=", "rootfstype=", "rootwait")
    kept = [arg for arg in sd_cmdline.split() if not arg.startswith(sd_root_args)]
    # ip=<client>:<server>:<gateway>:<netmask>:<hostname>:<device>:<autoconf>:<dns0>
    ip = (f"ip={address}:{server}:{network['gateway']}:{network['netmask']}"
          f"::eth0:off:{network['dns']}")
    nfs = ["root=/dev/nfs", f"nfsroot={server}:{nfs_path},vers=3,tcp", ip, "rw"]
    return " ".join(kept + nfs)


def require_nosuid_nodev(path):
    """Root extracts the caller's build output below `path`, owners, modes
    and extended attributes included. On a mount without nosuid and nodev, a
    set-user-ID file, a file capability or a device node in that output
    would give whoever runs this tool root on this host; with them, those
    only take effect on the Pi, which mounts the files over NFS (docs/lab.md,
    docs/adr/0005)."""
    flags = os.statvfs(path).f_flag
    if not (flags & os.ST_NOSUID and flags & os.ST_NODEV):
        sys.exit(f"refusing to serve from {path}: it must be on a mount with nosuid "
                 f"and nodev (docs/lab.md, build host setup)")


def replace_directory(path, parent):
    """Empties `path` (a directory inside `parent`), creating it if needed."""
    path, parent = path.resolve(), parent.resolve()
    if path.parent != parent or parent == Path("/"):
        sys.exit(f"refusing to replace {path}: not directly inside {parent}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def boot_partition(table):
    """The partition to take the boot files from: slot A's boot partition of
    the A/B layout (GPT name bootA, docs/adr/0008), else the first one."""
    partitions = table["partitions"]
    named = [p for p in partitions if p.get("name") == BOOT_PARTITION_NAME]
    return named[0] if named else partitions[0]


def copy_boot_partition(wic_bz2, destination):
    """Copies all files of the image's boot partition (FAT) into
    `destination`, without mounting anything."""
    with tempfile.TemporaryDirectory() as tmp:
        wic = Path(tmp) / "image.wic"
        with bz2.open(wic_bz2) as src, open(wic, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        table = json.loads(subprocess.run(
            ["sfdisk", "--json", str(wic)], capture_output=True, text=True, check=True,
        ).stdout)["partitiontable"]
        offset = boot_partition(table)["start"] * table.get("sectorsize", 512)
        subprocess.run(
            ["mcopy", "-s", "-p", "-n", "-i", f"{wic}@@{offset}", "::*", str(destination)],
            check=True, env={**os.environ, "MTOOLS_SKIP_CHECK": "1"},
        )


def make_world_readable(path):
    """TFTP (dnsmasq) serves as an unprivileged user."""
    subprocess.run(["chmod", "-R", "a+rX", str(path)], check=True)


def deploy(config, image, images_dir):
    stem = f"{image}-{MACHINE}.rootfs"
    wic = images_dir / f"{stem}.wic.bz2"
    rootfs = images_dir / f"{stem}.tar.zst"
    for artifact in (wic, rootfs):
        if not artifact.is_file():
            sys.exit(f"not built: {artifact}")

    netboot = config["netboot"]
    tftp_root, nfs_root = Path(netboot["tftp_root"]), Path(netboot["nfs_root"])
    nfs_dir = nfs_root / image
    tftp_dir = tftp_root / mac_directory(config["pi5"]["mac"])
    for served in (nfs_root, tftp_root):
        require_nosuid_nodev(served)

    # Root filesystem first: boot files must never point at a half-extracted one.
    print(f"root filesystem -> {nfs_dir}")
    replace_directory(nfs_dir, nfs_root)
    subprocess.run(
        ["tar", "--zstd", "--numeric-owner", "--xattrs", "--xattrs-include=*",
         "-xpf", str(rootfs), "-C", str(nfs_dir)],
        check=True,
    )

    # Boot files are prepared next to the served directory and swapped in.
    print(f"boot files -> {tftp_dir}")
    staging = tftp_root / f"{tftp_dir.name}.new"
    replace_directory(staging, tftp_root)
    copy_boot_partition(wic, staging)
    cmdline = staging / "cmdline.txt"
    cmdline.write_text(netboot_cmdline(
        cmdline.read_text(), netboot["server"], nfs_dir, config["pi5"]["address"], config["network"],
    ) + "\n")
    make_world_readable(staging)
    if tftp_dir.exists():
        shutil.rmtree(tftp_dir)
    staging.rename(tftp_dir)
    print(f"cmdline.txt: {(tftp_dir / 'cmdline.txt').read_text().strip()}")


def remove(config):
    tftp_dir = Path(config["netboot"]["tftp_root"]) / mac_directory(config["pi5"]["mac"])
    if tftp_dir.exists():
        shutil.rmtree(tftp_dir)
        print(f"removed {tftp_dir}; the Pi boots from its SD card")
    else:
        print(f"nothing served at {tftp_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path,
                        help=f"lab configuration (default: {REPO_CONFIG.name} of the "
                             f"checkout, else {SYSTEM_CONFIG})")
    commands = parser.add_subparsers(dest="command", required=True)
    deploy_cmd = commands.add_parser("deploy", help="serve an image to the Pi")
    deploy_cmd.add_argument("image", choices=IMAGES)
    deploy_cmd.add_argument("--images", required=True,
                            help="the build's deploy directory for raspberrypi5")
    commands.add_parser("remove", help="stop serving; the Pi boots from its SD card")
    args = parser.parse_args()

    if args.config and os.geteuid() == 0 and "SUDO_USER" in os.environ:
        # Through sudo a chosen configuration would choose which directories
        # root replaces; the sudo rule for CI must not allow that.
        sys.exit("--config is not accepted through sudo")
    config_path = args.config or (REPO_CONFIG if REPO_CONFIG.is_file() else SYSTEM_CONFIG)
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        sys.exit(f"lab configuration not found: {config_path} (docs/lab.md)")
    if args.command == "deploy":
        if not args.images.strip():
            sys.exit("--images is empty: is LG_IMAGES set in this shell?")
        images = Path(args.images)
        if not images.is_dir():
            sys.exit(f"--images {images} is not a directory")
        deploy(config, args.image, images)
    else:
        remove(config)


if __name__ == "__main__":
    main()
