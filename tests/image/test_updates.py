"""The A/B update system as built: SD card layout, boot slot images, update
bundles and what the images install for them (docs/adr/0008). Checked on
the build output, without booting; tests/update installs and boots it.

Host tools: mtools (FAT images) and squashfs-tools (bundles), docs/lab.md.
"""

import pytest

import helpers

pytestmark = pytest.mark.skipif(helpers.MACHINE != "raspberrypi5",
                                reason="A/B updates are on the Raspberry Pi 5")

MIB = 1024 * 1024
FAT = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
# Name, GPT type and size of each partition, in order: the layout that
# system.conf, edgenode-boot and edgenode-bootfs share with the wks file.
LAYOUT = [
    ("autoboot", FAT, 16 * MIB),
    ("bootA", FAT, 128 * MIB),
    ("bootB", FAT, 128 * MIB),
    ("rootA", LINUX, 1024 * MIB),
    ("rootB", LINUX, 1024 * MIB),
    ("data", LINUX, 256 * MIB),
]
AUTOBOOT_A = b"[all]\ntryboot_a_b=1\nboot_partition=2\n[tryboot]\nboot_partition=3\n"
IMAGES = ("edgenode-image", "edgenode-image-dev")


@pytest.fixture(scope="module")
def prod(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image")


@pytest.fixture(scope="module")
def dev(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image-dev")


@pytest.fixture(scope="module")
def bootfs(deploy_dir):
    return deploy_dir / "edgenode-bootfs-raspberrypi5.vfat"


@pytest.mark.parametrize("image", IMAGES)
def test_sd_card_layout(deploy_dir, image):
    wic = deploy_dir / f"{image}-raspberrypi5.rootfs.wic.bz2"
    partitions = helpers.gpt_partitions(wic)
    assert [(p.name, p.type, p.size) for p in partitions] == LAYOUT
    # Aligned to 4 MiB, as the SD card's erase blocks want.
    assert all(p.start % (4 * MIB) == 0 for p in partitions)


def test_autoboot_partition_selects_slot_a(deploy_dir):
    image = deploy_dir / "edgenode-autoboot-raspberrypi5.vfat"
    assert helpers.fat_files(image) == {"autoboot.txt"}
    # edgenode-boot rewrites this text in place and refuses any other.
    assert helpers.fat_read(image, "autoboot.txt") == AUTOBOOT_A


def test_each_slot_boots_its_own_root_filesystem(bootfs):
    common = helpers.fat_read(bootfs, "cmdline.txt").decode().split()
    for slot, root in (("A", "/dev/mmcblk0p4"), ("B", "/dev/mmcblk0p5")):
        cmdline = helpers.fat_read(bootfs, f"cmdline-{slot}.txt").decode().split()
        assert f"root={root}" in cmdline
        assert f"rauc.slot={slot}" in cmdline
        # The rest is the built command line: console, panic reboot.
        assert [a for a in cmdline if not a.startswith(("root=", "rauc.slot="))] == \
            [a for a in common if not a.startswith("root=")]
    assert "panic=10" in common
    assert "console=serial0,115200" in common
    # The fallback for firmware that cannot pick a file per slot: slot A.
    assert "root=/dev/mmcblk0p4" in common

    config = helpers.fat_read(bootfs, "config.txt").decode().splitlines()
    block = ["[boot_partition=2]", "cmdline=cmdline-A.txt",
             "[boot_partition=3]", "cmdline=cmdline-B.txt", "[all]"]
    start = config.index(block[0])
    assert config[start:start + len(block)] == block


def test_boot_slot_has_the_boot_files(bootfs, deploy_dir):
    files = helpers.fat_files(bootfs)
    assert {"kernel_2712.img", "bcm2712-rpi-5-b.dtb", "config.txt",
            "overlays/edgenode-ads1115.dtbo", "overlays/mipi-dbi-spi.dtbo"} <= files
    # The same bytes as the BSP's deployed files.
    assert helpers.fat_read(bootfs, "config.txt") == \
        (deploy_dir / "bootfiles" / "config.txt").read_bytes()


@pytest.mark.parametrize("bundle, image", [
    ("edgenode-bundle", "edgenode-image"),
    ("edgenode-bundle-dev", "edgenode-image-dev"),
])
def test_bundle_carries_this_builds_slots(deploy_dir, bootfs, bundle, image):
    manifest = helpers.bundle_manifest(deploy_dir / f"{bundle}-raspberrypi5.raucb")
    assert manifest["update"]["compatible"] == "edgenode-raspberrypi5"
    assert manifest["bundle"]["format"] == "verity"
    assert set(manifest) == {"update", "bundle", "image.rootfs", "image.bootfs"}

    rootfs, boot = manifest["image.rootfs"], manifest["image.bootfs"]
    assert rootfs["filename"] == "rootfs.ext4"
    assert boot["filename"] == "bootfs.vfat"
    # Byte for byte what the SD card image's slot A has.
    assert rootfs["sha256"] == helpers.sha256_file(helpers.Image(deploy_dir, image).ext4)
    assert boot["sha256"] == helpers.sha256_file(bootfs)
    assert int(boot["size"]) == LAYOUT[1][2]


def test_update_client_in_both_images(prod, dev):
    for image in (prod, dev):
        assert {"rauc", "rauc-service", "rauc-conf", "edgenode-boot"} <= image.packages
        # Not needed: squashfs-tools (bundles are mounted by the kernel),
        # rauc-mark-good (edgenode-boot-commit decides), download support.
        assert not {"squashfs-tools", "rauc-mark-good", "curl", "libcurl4"} & image.packages
        assert image.read_bytes("/usr/lib/rauc/ca.cert.pem").startswith(b"-----BEGIN CERTIFICATE")
        # The data partition's mount and the slot commit start from a
        # generator, on boots from a slot only.
        assert image.read_bytes(
            "/usr/lib/systemd/system-generators/edgenode-boot-generator").startswith(b"#!/bin/sh")


def test_rauc_system_configuration(prod):
    lines = {line.strip() for line in prod.read_file("/usr/lib/rauc/system.conf").splitlines()}
    assert {"compatible=edgenode-raspberrypi5", "bootloader=custom",
            "bootloader-custom-backend=/usr/bin/edgenode-boot",
            "bundle-formats=verity", "check-purpose=codesign"} <= lines
    assert {"device=/dev/mmcblk0p4", "device=/dev/mmcblk0p5",
            "device=/dev/mmcblk0p2", "device=/dev/mmcblk0p3"} <= lines


def test_hardware_watchdog_configured(prod):
    conf = prod.read_file("/usr/lib/systemd/system.conf.d/10-watchdog-edgenode.conf")
    assert "RuntimeWatchdogSec=15s" in conf.splitlines()
