"""A/B updates on the Raspberry Pi 5's SD card (docs/adr/0008).

These tests rewrite the SD card. They start from the development image
booted over the network, write the build's SD card image to the card from
there, stop the network boot and boot the card. Then they update, roll back
and cut the power, in order: each test starts from the state the one before
left.

    sudo tools/netboot.py deploy edgenode-image-dev --images "$LG_IMAGES"
    pytest --lg-env tests/env/pi5-dev.yaml tests/update

The target option netboot_command in the labgrid environment is the command
that stops the network boot (`<netboot_command> remove`).
"""

import shlex
import subprocess

import pytest

import helpers
from slots import DEVICE, login, power_cycle


@pytest.fixture(scope="module")
def ssh_target(target):
    address = target.get_resource("NetworkService").address
    connection = None

    def connect():
        nonlocal connection
        if connection:
            connection.close()
        helpers.wait_for_ssh_banner(address, 22)
        connection = helpers.TargetSSH(address)
        return connection

    yield connect
    if connection:
        connection.close()


@pytest.fixture(scope="module")
def bundle(deploy_dir):
    path = deploy_dir / "edgenode-bundle-dev-raspberrypi5.raucb"
    if not path.exists():
        pytest.fail(f"not built: {path}")
    return path


@pytest.fixture(scope="module")
def sd_card(target, deploy_dir, ssh_target):
    """The SD card, freshly written with the build's development image, and
    booted: slot A, committed."""
    wic = deploy_dir / "edgenode-image-dev-raspberrypi5.rootfs.wic.bz2"
    bmap = deploy_dir / "edgenode-image-dev-raspberrypi5.rootfs.wic.bmap"
    for path in (wic, bmap):
        if not path.exists():
            pytest.fail(f"not built: {path}")

    power_cycle(target)
    shell = login(target)
    if "nfsroot=" not in shell.run_check("cat /proc/cmdline")[0]:
        pytest.fail("the Pi must boot the development image over the network first "
                    "(tools/netboot.py deploy edgenode-image-dev)")
    ssh_target().write_image(wic, bmap, DEVICE)

    netboot = shlex.split(helpers.target_option(target, "netboot_command"))
    subprocess.run([*netboot, "remove"], check=True)
    power_cycle(target)
    return login(target)
