"""The A/B update system as built: SD card layout, boot slot images, the
dm-verity root filesystems, update bundles and what the images install for
them (docs/adr/0008, docs/adr/0009). Checked on the build output, without
booting; tests/update installs and boots it.

Host tools: mtools (FAT images), squashfs-tools (bundles) and veritysetup
(cryptsetup-bin), docs/lab.md.
"""

import bz2
import hashlib
import shlex
import shutil
import struct
import subprocess
from pathlib import Path

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
# Each bundle and the image it carries. Release builds make no development
# bundle (kas/release.yml).
BUNDLES = [
    ("edgenode-bundle", "edgenode-image"),
    pytest.param("edgenode-bundle-dev", "edgenode-image-dev", marks=pytest.mark.skipif(
        helpers.RELEASE, reason="release builds make no development bundle")),
]


@pytest.fixture(scope="module")
def prod(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image")


@pytest.fixture(scope="module")
def dev(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image-dev")


def image_file(deploy_dir, image, suffix):
    """A file the image's verity type writes: verity, verity.env, bootfs.vfat."""
    return deploy_dir / f"{image}-raspberrypi5.rootfs.{suffix}"


def verity_env(deploy_dir, image):
    lines = image_file(deploy_dir, image, "verity.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def kernel_args(bootfs, name):
    """A command line file of a boot slot image, split as the kernel splits it
    (dm-mod.create="..." is one argument)."""
    return shlex.split(helpers.fat_read(bootfs, name).decode())


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


def card_after_block_map_write(wic, bmap, partition, path):
    """Writes to path what a used SD card holds in one partition after the
    image is written with its block map: the image's bytes in the ranges
    the map lists, the card's old data (here 0xa5) everywhere else."""
    view = bytearray(b"\xa5" * partition.size)
    start, end = partition.start, partition.start + partition.size
    _, ranges = helpers.bmap_ranges(bmap)
    with bz2.open(wic) as image:
        for offset, length, _ in ranges:
            low, high = max(offset, start), min(offset + length, end)
            if low < high:
                image.seek(low)
                view[low - start:high - start] = image.read(high - low)
    path.write_bytes(view)


@pytest.mark.parametrize("image", IMAGES)
def test_boot_slots_read_the_same_from_a_used_card(deploy_dir, tmp_path, image):
    # The block map leaves out the image's holes, so a card written with it
    # keeps its old data there. The firmware must still read every boot file
    # as built: a hole inside a file would hand it the old bytes. (A boot
    # slot copied with cp --sparse=always once turned the zero runs in the
    # kernel into holes, and the card did not boot.)
    wic = deploy_dir / f"{image}-raspberrypi5.rootfs.wic.bz2"
    bmap = deploy_dir / f"{image}-raspberrypi5.rootfs.wic.bmap"
    bootfs = image_file(deploy_dir, image, "bootfs.vfat")
    files = sorted(helpers.fat_files(bootfs))
    for partition in helpers.gpt_partitions(wic):
        if partition.name not in ("bootA", "bootB"):
            continue
        card = tmp_path / f"{partition.name}.vfat"
        card_after_block_map_write(wic, bmap, partition, card)
        assert helpers.fat_files(card) == set(files)
        changed = [name for name in files
                   if helpers.fat_read(card, name) != helpers.fat_read(bootfs, name)]
        assert not changed, f"{partition.name} reads differently on a used card: {changed}"


@pytest.mark.parametrize("image", IMAGES)
def test_each_slot_boots_its_own_verified_root_filesystem(deploy_dir, image):
    bootfs = image_file(deploy_dir, image, "bootfs.vfat")
    env = verity_env(deploy_dir, image)
    blocks, salt, roothash = env["VERITY_DATA_BLOCKS"], env["VERITY_SALT"], env["VERITY_ROOT_HASH"]
    common = kernel_args(bootfs, "cmdline.txt")
    for slot, dev in (("A", "/dev/mmcblk0p4"), ("B", "/dev/mmcblk0p5")):
        cmdline = kernel_args(bootfs, f"cmdline-{slot}.txt")
        # The kernel sets the verity device up over the slot's partition,
        # with this image's root hash, and mounts it read-only.
        table = (f"0 {int(blocks) * 8} verity 1 {dev} {dev} 4096 4096 {blocks} {blocks} "
                 f"sha256 {roothash} {salt} 1 ignore_zero_blocks")
        ours = [f"dm-mod.create=vroot,,,ro,{table}", f"dm-mod.waitfor={dev}",
                "root=/dev/dm-0", "ro", f"rauc.slot={slot}"]
        assert set(ours) <= set(cmdline), cmdline
        # The rest is the built command line: console, panic reboot.
        assert [a for a in cmdline if a not in ours] == \
            [a for a in common if not a.startswith("root=")]
    assert "panic=10" in common
    assert "console=serial0,115200" in common
    # The fallback for firmware that cannot pick a file per slot: slot A,
    # without verification.
    assert "root=/dev/mmcblk0p4" in common

    config = helpers.fat_read(bootfs, "config.txt").decode().splitlines()
    block = ["[boot_partition=2]", "cmdline=cmdline-A.txt",
             "[boot_partition=3]", "cmdline=cmdline-B.txt", "[all]"]
    start = config.index(block[0])
    assert config[start:start + len(block)] == block


@pytest.mark.parametrize("image", IMAGES)
def test_root_filesystem_blocks_fit_the_verity_device(deploy_dir, image):
    # The verity device's sectors are its data block size, and ext4 does not
    # mount with smaller blocks: the kernel panics with "bad block size".
    with open(helpers.Image(deploy_dir, image).ext4, "rb") as f:
        f.seek(1024)
        superblock = f.read(1024)
    assert struct.unpack_from("<H", superblock, 56)[0] == 0xEF53
    block_size = 1024 << struct.unpack_from("<I", superblock, 24)[0]
    assert block_size == int(verity_env(deploy_dir, image)["VERITY_BLOCK_SIZE"])


@pytest.mark.parametrize("image", IMAGES)
def test_verity_image_matches_the_root_hash_it_boots_with(deploy_dir, image):
    # The hash tree appended to the ext4 checks out against the root hash on
    # the boot slot's command line, as the kernel will check it.
    veritysetup = shutil.which("veritysetup") or "/usr/sbin/veritysetup"
    verity = image_file(deploy_dir, image, "verity")
    ext4 = helpers.Image(deploy_dir, image).ext4
    args = kernel_args(image_file(deploy_dir, image, "bootfs.vfat"), "cmdline-A.txt")
    table = next(a for a in args if a.startswith("dm-mod.create=")).split(",", 4)[4].split()
    blocks, salt, roothash = int(table[8]), table[12], table[11]
    size = ext4.stat().st_size
    assert blocks * 4096 == size
    subprocess.run(
        [veritysetup, "verify", "--no-superblock", "--hash=sha256",
         "--data-block-size=4096", "--hash-block-size=4096", f"--data-blocks={blocks}",
         f"--hash-offset={size}", f"--salt={salt}", str(verity), str(verity), roothash],
        check=True, capture_output=True,
    )
    # The data area is the ext4 image itself.
    digest = hashlib.sha256()
    with open(verity, "rb") as f:
        for _ in range(size // (1 << 20)):
            digest.update(f.read(1 << 20))
        digest.update(f.read(size % (1 << 20)))
    assert digest.hexdigest() == helpers.sha256_file(ext4)


@pytest.mark.parametrize("image", IMAGES)
def test_boot_slot_has_the_boot_files(deploy_dir, image):
    bootfs = image_file(deploy_dir, image, "bootfs.vfat")
    files = helpers.fat_files(bootfs)
    assert {"kernel_2712.img", "bcm2712-rpi-5-b.dtb", "config.txt", "cmdline.txt",
            "cmdline-A.txt", "cmdline-B.txt",
            "overlays/edgenode-ads1115.dtbo", "overlays/mipi-dbi-spi.dtbo"} <= files
    # The same bytes as the BSP's deployed files.
    assert helpers.fat_read(bootfs, "config.txt") == \
        (deploy_dir / "bootfiles" / "config.txt").read_bytes()


@pytest.mark.parametrize("bundle, image", BUNDLES)
def test_bundle_carries_this_builds_slots(deploy_dir, bundle, image):
    manifest = helpers.bundle_manifest(deploy_dir / f"{bundle}-raspberrypi5.raucb")
    assert manifest["update"]["compatible"] == "edgenode-raspberrypi5"
    assert manifest["bundle"]["format"] == "verity"
    assert set(manifest) == {"update", "bundle", "image.rootfs", "image.bootfs"}

    rootfs, boot = manifest["image.rootfs"], manifest["image.bootfs"]
    assert rootfs["filename"] == "rootfs.img"
    assert boot["filename"] == "bootfs.vfat"
    # Byte for byte what the SD card image's slot A has: the verity root
    # filesystem and the boot slot image with its root hash.
    assert rootfs["sha256"] == helpers.sha256_file(image_file(deploy_dir, image, "verity"))
    assert boot["sha256"] == helpers.sha256_file(image_file(deploy_dir, image, "bootfs.vfat"))
    assert int(boot["size"]) == LAYOUT[1][2]


@pytest.mark.parametrize("bundle, image", BUNDLES)
def test_bundle_signed_for_the_images_keyring(deploy_dir, tmp_path, bundle, image):
    # What RAUC checks before it installs: the signature chains to the CA
    # certificate in the image (its keyring), and the signer is meant for
    # code signing (check-purpose=codesign).
    keyring = tmp_path / "keyring.pem"
    keyring.write_bytes(helpers.Image(deploy_dir, image).read_bytes("/usr/lib/rauc/ca.cert.pem"))
    path = deploy_dir / f"{bundle}-raspberrypi5.raucb"
    signed, signer = helpers.bundle_signature(path, keyring, tmp_path)
    usage = subprocess.run(["openssl", "x509", "-noout", "-ext", "extendedKeyUsage"],
                           input=signer, capture_output=True, check=True, text=True).stdout
    assert "Code Signing" in usage
    # The signed manifest is the one in the bundle, plus the verity root
    # hash that protects the bundle's squashfs.
    manifest = helpers.bundle_manifest(path)
    assert {k: v for k, v in signed.items() if k != "bundle"} == \
        {k: v for k, v in manifest.items() if k != "bundle"}
    assert {"verity-hash", "verity-salt", "verity-size"} <= set(signed["bundle"])


@pytest.mark.skipif(helpers.CA_CERT is None, reason="no expected CA given (EDGENODE_CA_CERT)")
@pytest.mark.parametrize("image", IMAGES)
def test_images_trust_the_expected_ca(deploy_dir, image):
    # CI names the CA its keys come from: the development CA, or in a
    # release the release CA (docs/releases.md).
    keyring = helpers.Image(deploy_dir, image).read_bytes("/usr/lib/rauc/ca.cert.pem")
    assert keyring == Path(helpers.CA_CERT).read_bytes(), f"{image} trusts another CA than {helpers.CA_CERT}"


@pytest.mark.skipif(not helpers.RELEASE, reason="not a release build (EDGENODE_RELEASE)")
def test_release_build_signs_no_development_bundle(deploy_dir):
    assert not list(deploy_dir.glob("edgenode-bundle-dev*"))


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
            "bundle-formats=verity", "check-purpose=codesign",
            "mountprefix=/run/rauc"} <= lines
    assert {"device=/dev/mmcblk0p4", "device=/dev/mmcblk0p5",
            "device=/dev/mmcblk0p2", "device=/dev/mmcblk0p3"} <= lines


def test_images_run_from_a_read_only_root(prod, dev):
    # read-only-rootfs: sshd makes its host keys in /var/run at each boot,
    # since /etc cannot be written.
    for image in (prod, dev):
        assert "sshd_config_readonly" in image.read_file("/etc/default/ssh")


def test_hardware_watchdog_configured(prod):
    conf = prod.read_file("/usr/lib/systemd/system.conf.d/10-watchdog-edgenode.conf")
    assert "RuntimeWatchdogSec=15s" in conf.splitlines()
