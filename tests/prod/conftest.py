"""Production image: nobody can log in, so the tests boot it and wait for
the login prompt on the serial console, then work from the outside."""

import pytest

LOGIN_PROMPT = r"login: "


@pytest.fixture(scope="session")
def console(target):
    """The serial console of the freshly booted production image.

    In QEMU one driver is both console and power switch. On hardware the
    console is opened before power-cycling, so no boot output is missed.
    """
    console = target.get_driver("ConsoleProtocol")
    power = target.get_driver("PowerProtocol")
    power.cycle()
    # Full emulation of Arm on x86 boots slowly.
    console.expect(LOGIN_PROMPT, timeout=300)
    return console


@pytest.fixture(scope="session")
def booted(console):
    return console
