"""Checks on the built images themselves, without booting them: installed
packages from the manifests, files read straight from the ext4 images."""

import re

import pytest

import helpers

DEBUG_TOOLS = {"gdbserver", "strace"}
# Package names of the Mesa/OpenGL, Wayland and X11 stacks. Precise on
# purpose: a plain "libgl" prefix also matches GLib (libglib-2.0-0).
GRAPHICS_PACKAGE = re.compile(
    r"^(mesa|libgl-|libgl1|libglapi|libglvnd|libglx|libegl|libgles|libgbm|libwayland|wayland|libx11|xserver)"
)
# Wi-Fi and Bluetooth firmware from the Raspberry Pi BSP. The node is wired
# only (docs/adr/0003).
WIRELESS_FIRMWARE = re.compile(r"^(linux-firmware-rpidistro|bluez-firmware)")


@pytest.fixture(scope="module")
def prod(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image")


@pytest.fixture(scope="module")
def dev(deploy_dir):
    return helpers.Image(deploy_dir, "edgenode-image-dev")


def test_debug_tools_only_in_dev(prod, dev):
    assert DEBUG_TOOLS <= dev.packages
    assert not DEBUG_TOOLS & prod.packages


def test_root_account_locked_in_prod(prod, dev):
    prod_password = prod.shadow_entry("root")[1]
    assert prod_password.startswith(("*", "!")), f"root password field: {prod_password!r}"
    assert dev.shadow_entry("root")[1] == ""  # control


def test_sshd_policy(prod, dev):
    prod_cfg = prod.sshd_settings()
    # Unset means the OpenSSH default: no empty passwords, root by key only.
    assert prod_cfg.get("permitemptypasswords", "no") == "no"
    assert prod_cfg.get("permitrootlogin", "prohibit-password") != "yes"

    dev_cfg = dev.sshd_settings()  # control
    assert dev_cfg.get("permitemptypasswords") == "yes"
    assert dev_cfg.get("permitrootlogin") == "yes"


def test_no_graphics_stack(prod, dev):
    for image in (prod, dev):
        offenders = sorted(p for p in image.packages if GRAPHICS_PACKAGE.match(p))
        assert not offenders, f"{image.manifest.name}: {offenders}"


def test_no_wireless_firmware(prod, dev):
    for image in (prod, dev):
        offenders = sorted(p for p in image.packages if WIRELESS_FIRMWARE.match(p))
        assert not offenders, f"{image.manifest.name}: {offenders}"


def test_both_images_run_edgenoded(prod, dev):
    for image in (prod, dev):
        assert {"edgenoded", "edgenode-config"} <= image.packages


@pytest.mark.skipif(helpers.MACHINE != "raspberrypi5", reason="Raspberry Pi 5 boot files")
def test_raspberrypi5_boot_files(deploy_dir):
    # The boot partition's files, as deployed for the SD card image.
    bootfiles = deploy_dir / "bootfiles"
    config = [line.strip() for line in (bootfiles / "config.txt").read_text().splitlines()]
    cmdline = (bootfiles / "cmdline.txt").read_text().split()

    # Console on the 40-pin header's UART, where the serial cable is.
    assert "dtparam=uart0_console" in config
    assert "console=serial0,115200" in cmdline
    # Both radios off (docs/adr/0003).
    assert "dtoverlay=disable-wifi-pi5" in config
    assert "dtoverlay=disable-bt-pi5" in config
    # The ADS1115 on the header's I2C bus, through our own driver's overlay.
    assert "dtparam=i2c_arm=on" in config
    assert "dtoverlay=edgenode-ads1115" in config
    assert (deploy_dir / "devicetree" / "edgenode-ads1115.dtbo").is_file()
    # The ST7789 display on SPI0, chip select tied low on the module (mode 3).
    assert "dtoverlay=mipi-dbi-spi,spi0-0,cpha,cpol,write-only" in config
    assert "dtparam=width=240,height=240,reset-gpio=23,dc-gpio=24" in config
    assert "dtparam=backlight-gpio=18" in config
    assert any(deploy_dir.glob("**/mipi-dbi-spi.dtbo")), "mipi-dbi-spi overlay not deployed"


@pytest.mark.skipif(helpers.MACHINE != "raspberrypi5", reason="Raspberry Pi 5 ADC")
def test_raspberrypi5_adc_driver_installed(prod, dev):
    # Our driver only: the mainline one left after the comparison (docs/adr/0004).
    for image in (prod, dev):
        assert has_kernel_module(image, "edgenode-ads1115")
        assert not has_kernel_module(image, "ti-ads1015")


def has_kernel_module(image, module):
    # Kernel module packages carry the kernel version: oe-core names them
    # kernel-module-<module>-<kernel version> (KERNEL_MODULE_PACKAGE_SUFFIX).
    pattern = re.compile(rf"^kernel-module-{re.escape(module)}-\d")
    return any(pattern.match(package) for package in image.packages)


@pytest.mark.skipif(helpers.MACHINE != "raspberrypi5", reason="Raspberry Pi 5 display")
def test_raspberrypi5_panel_firmware(prod, dev):
    # The display's init sequence, compiled at build time; the driver checks
    # the same header (drivers/gpu/drm/tiny/panel-mipi-dbi.c). /usr/lib, as
    # systemd brings usrmerge.
    for image in (prod, dev):
        firmware = image.read_bytes("/usr/lib/firmware/panel.bin")
        assert firmware[:16] == b"MIPI DBI" + bytes(7) + b"\x01", firmware[:16]
