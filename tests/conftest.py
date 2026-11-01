"""Fixtures shared by the integration tests.

The tests only talk to the target through labgrid drivers, so the same tests
run against QEMU (tests/env/qemu.yaml) and, later, real hardware.
"""

import socket

import pytest
from labgrid.exceptions import NoDriverFoundError

EDGENODED_PORT = 8080


@pytest.fixture(scope="session")
def shell(strategy):
    """A root shell on the booted target, after boot has finished.

    Boots once per test session. `is-system-running --wait` returns when
    systemd has finished starting units, whatever the outcome; the boot
    tests check the outcome.
    """
    strategy.transition("shell")
    driver = strategy.target.get_driver("ShellDriver")
    driver.run("dmesg -n 1")  # keep kernel messages off the console
    driver.run("systemctl is-system-running --wait", timeout=300)
    return driver


def _free_tcp_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def edgenoded_url(target, shell):
    """Base URL of edgenoded as reached from the test host."""
    try:
        qemu = target.get_driver("QEMUDriver")
    except NoDriverFoundError:
        pytest.skip("edgenoded_url is only implemented for QEMU so far")

    # User-mode networking: forward a free host port to the guest.
    port = _free_tcp_port()
    qemu.add_port_forward("tcp", "127.0.0.1", port, "", EDGENODED_PORT)
    yield f"http://127.0.0.1:{port}"
    qemu.remove_port_forward("tcp", "127.0.0.1", port)
