"""Production image: nobody can log in, so the tests boot it and wait for
the login prompt on the serial console, then work from the outside."""

import pytest

LOGIN_PROMPT = r"login: "


@pytest.fixture(scope="session")
def console(target):
    """The QEMU serial console of the booted production image."""
    qemu = target.get_driver("QEMUDriver")
    qemu.on()
    # Full emulation of Arm on x86 boots slowly.
    qemu.expect(LOGIN_PROMPT, timeout=300)
    return qemu


@pytest.fixture(scope="session")
def booted(console):
    return console
