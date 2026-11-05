"""ST7789 display (240x240, SPI) on the Raspberry Pi 5, driven by the kernel's
panel-mipi-dbi driver.

The panel is write-only, so its pixels cannot be read back. The tests check
what the kernel reports: the panel registered as a DRM display with its
mode, which also means its init sequence (panel.bin) loaded, and a backlight
that follows its control. Looking at the picture is a manual check
(docs/lab.md). Runs only where the labgrid environment lists st7789.
"""

import pytest

pytestmark = pytest.mark.lg_feature("st7789")


@pytest.fixture(scope="module")
def connector(shell):
    """sysfs directory of the panel's DRM connector (SPI panels: card*-SPI-*)."""
    out, _, code = shell.run("ls -1d /sys/class/drm/card*-SPI-*")
    assert code == 0 and len(out) == 1, f"expected one SPI display connector, found: {out}"
    return out[0]


@pytest.fixture(scope="module")
def backlight(shell):
    """sysfs directory of the panel's backlight (driver gpio-backlight)."""
    out = shell.run_check(
        "for b in /sys/class/backlight/*; do "
        '[ "$(basename "$(readlink -f "$b/device/driver")")" = gpio-backlight ] && echo "$b"; '
        "done; true"
    )
    assert len(out) == 1, f"expected one GPIO backlight, found: {out}"
    return out[0]


def test_panel_driver_bound(shell, connector):
    driver = shell.run_check(f"readlink -f {connector}/device/device/driver")[0]
    assert driver.rsplit("/", 1)[-1] == "panel-mipi-dbi-spi"


def test_connected_with_panel_mode(shell, connector):
    assert shell.run_check(f"cat {connector}/status") == ["connected"]
    assert "240x240" in shell.run_check(f"cat {connector}/modes")


def test_backlight_follows_control(shell, backlight):
    try:
        for level in ("0", "1"):
            shell.run_check(f"echo {level} > {backlight}/brightness")
            assert shell.run_check(f"cat {backlight}/actual_brightness") == [level]
    finally:
        shell.run(f"echo 1 > {backlight}/brightness")
