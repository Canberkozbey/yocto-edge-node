"""edgenoded serves analog readings at /sensors, read through IIO.

On every target, extra edgenoded instances read a fake IIO device made of
plain files, so reading, scaling and error reporting are checked in QEMU
too. On the Raspberry Pi 5 the service itself reads the ADS1115.
"""

import json
import shlex

import pytest

import helpers

WORK = "/tmp/edgenoded-sensors-test"
FAKE_IIO = f"{WORK}/adc/iio:device0"
INSTANCES = {  # name: (port, configuration lines)
    "inputs": (8081, [
        f"adc_device = {WORK}/adc",
        "channel.knob = 0",
        "channel.negative = 3",
        "channel.missing = 5",
    ]),
    "none": (8082, []),
}


@pytest.fixture(scope="module")
def instances(shell):
    shell.run_check(f"rm -rf {WORK} && mkdir -p '{FAKE_IIO}'")
    for channel, raw in ((0, 16520), (3, -8)):
        shell.run_check(f"echo {raw} > '{FAKE_IIO}/in_voltage{channel}_raw'")
        shell.run_check(f"echo 0.125000000 > '{FAKE_IIO}/in_voltage{channel}_scale'")

    for name, (port, lines) in INSTANCES.items():
        conf = f"{WORK}/{name}.conf"
        lines = ["bind = 127.0.0.1", f"port = {port}", *lines]
        shell.run_check(f"printf '%s\\n' {' '.join(shlex.quote(l) for l in lines)} > {conf}")
        # A subshell keeps the process out of the shell's job table.
        shell.run_check(f"(setsid edgenoded --config {conf} </dev/null "
                        f">{WORK}/{name}.log 2>&1 & echo $! > {WORK}/{name}.pid)")
    yield
    shell.run(f"kill $(cat {WORK}/*.pid) 2>/dev/null; rm -rf {WORK}")


def sensors(shell, name):
    port = INSTANCES[name][0]
    out = shell.run_check(
        f"for i in 1 2 3 4 5; do wget -qO- http://127.0.0.1:{port}/sensors && break; sleep 1; done"
    )
    return json.loads("".join(out))["sensors"]


def test_reads_and_scales_inputs(shell, instances):
    readings = sensors(shell, "inputs")
    assert readings["knob"] == {"channel": 0, "raw": 16520, "millivolts": 2065.0}
    assert readings["negative"] == {"channel": 3, "raw": -8, "millivolts": -1.0}


def test_unreadable_input_is_reported(shell, instances):
    readings = sensors(shell, "inputs")
    assert readings["missing"] == {"channel": 5, "error": "read failed"}
    assert "raw" in readings["knob"], "one bad input must not hide the others"


def test_no_inputs_configured(shell, instances):
    assert sensors(shell, "none") == {}


@pytest.mark.lg_feature("ads1115")
def test_service_reads_the_adc(edgenoded_url):
    # The service's own configuration on the Pi 5 (dynamic-layers), read from
    # the host. Same references and tolerances as tests/dev/test_adc.py.
    status, body = helpers.http_get(f"{edgenoded_url}/sensors")
    assert status == 200
    readings = json.loads(body)["sensors"]
    ground = readings["ground"]["millivolts"]
    supply = readings["supply"]["millivolts"]
    assert abs(ground) < 10
    assert 3300 * 0.95 <= supply <= 3300 * 1.05, f"3.3 V reference reads {supply:.0f} mV"
    assert ground - 10 <= readings["potentiometer"]["millivolts"] <= supply + 10
