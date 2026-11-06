"""edgenoded shows the readings on a DRM/KMS display.

In QEMU, vkms (a virtual KMS display) stands in for the panel: an extra
edgenoded instance reads a stand-in IIO device and draws on vkms's
connector. debugfs shows which process's framebuffer is on screen, and vkms
computes a CRC of every frame it composes, so a redraw can be seen without
reading pixels back. Runs where the labgrid environment lists vkms (the
QEMU development image, which has the module). On the Raspberry Pi 5 the
service draws on the ST7789 itself (test_display.py).
"""

import pytest

import helpers

pytestmark = pytest.mark.lg_feature("vkms")

WORK = "/tmp/edgenoded-screen-test"
FAKE_IIO = f"{WORK}/adc/iio:device0"
CONNECTOR = "Virtual-1"
NAME = "screen"


def set_raw(shell, value):
    shell.run_check(f"echo {value} > '{FAKE_IIO}/in_voltage0_raw'")


@pytest.fixture(scope="module")
def vkms(shell):
    """The vkms card's debugfs directory, with frame CRCs switched on."""
    shell.run_check("modprobe vkms")
    debugfs = helpers.drm_debugfs(shell, CONNECTOR)
    shell.run_check(f"echo auto > {debugfs}/crtc-0/crc/control")
    yield debugfs
    shell.run("modprobe -r vkms; true")


@pytest.fixture(scope="module")
def instance(shell, vkms):
    shell.run_check(f"rm -rf {WORK} && mkdir -p '{FAKE_IIO}'")
    set_raw(shell, 16520)
    shell.run_check(f"echo 0.125000000 > '{FAKE_IIO}/in_voltage0_scale'")
    helpers.start_edgenoded(shell, WORK, NAME, [
        "bind = 127.0.0.1",
        "port = 8083",
        f"adc_device = {WORK}/adc",
        "channel.knob = 0",
        f"display_connector = {CONNECTOR}",
    ])
    yield
    helpers.stop_edgenoded(shell, WORK, NAME)
    shell.run(f"rm -rf {WORK}")


def log(shell):
    return "\n".join(shell.run(f"cat {WORK}/{NAME}.log")[0])


def frame_crc(shell, debugfs):
    # Each read of crc/data returns one "<frame> <crc>" line, NUL-terminated.
    line = shell.run_check(f"head -n 3 {debugfs}/crtc-0/crc/data | tr -d '\\000' | tail -n 1")
    return line[0].split()[1]


def test_draws_on_the_display(shell, vkms, instance):
    assert helpers.wait_until(lambda: "edgenoded" in helpers.screen_owners(shell, vkms)), log(shell)
    assert helpers.screen_owners(shell, vkms) == ["edgenoded"]

    # In the connector's preferred mode (listed first), on an active CRTC.
    preferred = shell.run_check(f"head -n 1 /sys/class/drm/card*-{CONNECTOR}/modes")[0]
    state = [line.strip() for line in shell.run_check(f"cat {vkms}/state")]
    assert f"size={preferred}" in state
    assert "active=1" in state


def test_redraws_when_a_reading_changes(shell, vkms, instance):
    # Control: while nothing changes, every frame has the same CRC.
    before = frame_crc(shell, vkms)
    shell.run_check("sleep 2")
    assert frame_crc(shell, vkms) == before

    set_raw(shell, 8000)
    assert helpers.wait_until(lambda: frame_crc(shell, vkms) != before), \
        "the screen did not change after the reading did"


def test_gives_the_display_back_when_it_stops(shell, vkms, instance):
    helpers.stop_edgenoded(shell, WORK, NAME)
    assert helpers.wait_until(lambda: "edgenoded" not in helpers.screen_owners(shell, vkms)), \
        f"still on screen: {helpers.screen_owners(shell, vkms)}"
