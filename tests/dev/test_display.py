"""ST7789 display (240x240, SPI) on the Raspberry Pi 5, driven by the kernel's
panel-mipi-dbi driver.

The panel is write-only, so its pixels cannot be read back. The tests check
what the kernel reports: the panel registered as a DRM display with its
mode, which also means its init sequence (panel.bin) loaded, a backlight
that follows its control, and edgenoded's framebuffer on screen, given back
to the console when the service stops. Looking at the picture is a manual
check (docs/lab.md). Runs only where the labgrid environment lists st7789.
"""

import pytest

import helpers

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


@pytest.fixture(scope="module")
def debugfs(shell):
    return helpers.drm_debugfs(shell, "SPI-*")


def service_on_screen(shell, debugfs):
    return helpers.screen_owners(shell, debugfs) == ["edgenoded"]


def test_service_draws_on_the_panel(shell, debugfs):
    # The service opens the panel once the panel's driver has loaded.
    assert helpers.wait_until(lambda: service_on_screen(shell, debugfs)), \
        "\n".join(shell.run("journalctl -b -u edgenoded --no-pager")[0])
    state = [line.strip() for line in shell.run_check(f"cat {debugfs}/state")]
    assert "size=240x240" in state


def test_console_gets_the_panel_back_when_the_service_stops(shell, debugfs):
    try:
        shell.run_check("systemctl stop edgenoded")
        assert helpers.wait_until(lambda: "edgenoded" not in helpers.screen_owners(shell, debugfs))
    finally:
        shell.run_check("systemctl start edgenoded")
    assert helpers.wait_until(lambda: service_on_screen(shell, debugfs))

