"""ADS1115 ADC on the Raspberry Pi 5, read through the kernel's IIO interface.

A1 and A2 are wired to GND and 3.3 V (docs/lab.md): fixed references, so
their readings are known in advance. A0 is a potentiometer, so only its range
is checked. The same tests ran against our driver and the mainline one to
validate ours (docs/adr/0004). Runs only where the labgrid environment lists
the ads1115 feature.
"""

import pytest

pytestmark = pytest.mark.lg_feature("ads1115")

SUPPLY_MV = 3300
SUPPLY_TOLERANCE = 0.05  # the Pi's 3.3 V rail
GROUND_LIMIT_MV = 10
FULL_SCALE_MV = 4096  # gain 1, from the device tree overlay
RESOLUTION = 2 ** 15  # 16-bit, signed


# The ADC as an I2C device: bus 1 (the header's i2c_arm), address 0x48.
ADC_I2C_DEVICE = "/sys/bus/i2c/devices/1-0048"
DRIVERS = {"edgenode-ads1115", "ads1015"}  # ours, mainline


@pytest.fixture(scope="module")
def adc_device(shell):
    """sysfs directory of the ADC's IIO device.

    Found by the ADC's bus address, not by the IIO device name: the mainline
    driver names its device ads1015 for every chip it supports, ours ads1115.
    """
    out, _, code = shell.run(f"ls -1d {ADC_I2C_DEVICE}/iio:device*")
    assert code == 0 and len(out) == 1, f"expected one IIO device under {ADC_I2C_DEVICE}, found: {out}"
    return out[0]


@pytest.fixture(scope="module")
def read_mv(shell, adc_device):
    """Returns a function reading one single-ended channel, in millivolts."""
    def read(channel):
        raw = int(shell.run_check(f"cat {adc_device}/in_voltage{channel}_raw")[0])
        return raw * scale(shell, adc_device, channel)
    return read


def scale(shell, adc_device, channel):
    """Millivolts per count, as the driver reports it."""
    return float(shell.run_check(f"cat {adc_device}/in_voltage{channel}_scale")[0])


def test_driver(shell, record_property):
    # Which driver these readings came from, kept in the JUnit report.
    driver = shell.run_check(f"readlink -f {ADC_I2C_DEVICE}/driver")[0].rsplit("/", 1)[-1]
    record_property("adc_driver", driver)
    assert driver in DRIVERS


def test_channels_use_the_configured_full_scale(shell, adc_device):
    for channel in (0, 1, 2):
        assert scale(shell, adc_device, channel) == pytest.approx(FULL_SCALE_MV / RESOLUTION)


def test_ground_reference(read_mv):
    assert abs(read_mv(1)) < GROUND_LIMIT_MV


def test_supply_reference(read_mv):
    mv = read_mv(2)
    low, high = SUPPLY_MV * (1 - SUPPLY_TOLERANCE), SUPPLY_MV * (1 + SUPPLY_TOLERANCE)
    assert low <= mv <= high, f"3.3 V reference reads {mv:.0f} mV"


def test_potentiometer_between_references(read_mv):
    ground, supply = read_mv(1), read_mv(2)
    assert ground - GROUND_LIMIT_MV <= read_mv(0) <= supply + GROUND_LIMIT_MV
