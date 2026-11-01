"""Development image: boot and log in as root on the serial console."""

import pytest


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


@pytest.fixture(scope="session")
def booted(shell):
    return shell
