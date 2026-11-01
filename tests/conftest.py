"""Fixtures shared by all test directories.

Each directory defines `booted`: the target, booted far enough for the
network services to come up. dev/ boots and logs in on the console;
prod/ cannot log in and only waits for the login prompt.
"""

import os
from pathlib import Path

import pytest
from labgrid.exceptions import NoDriverFoundError

import helpers

EDGENODED_PORT = 8080
SSH_PORT = 22


@pytest.fixture(scope="session")
def deploy_dir():
    """The build's deploy directory (LG_IMAGES)."""
    path = os.environ.get("LG_IMAGES")
    if not path:
        pytest.fail("LG_IMAGES must point at the deploy directory")
    return Path(path)


@pytest.fixture(scope="session")
def host_port(target, booted):
    """Returns a function that forwards a guest TCP port to a free host port."""
    try:
        qemu = target.get_driver("QEMUDriver")
    except NoDriverFoundError:
        pytest.skip("port forwarding is only implemented for QEMU so far")

    forwarded = []

    def forward(guest_port):
        port = helpers.free_tcp_port()
        qemu.add_port_forward("tcp", "127.0.0.1", port, "", guest_port)
        forwarded.append(port)
        return port

    yield forward
    for port in forwarded:
        qemu.remove_port_forward("tcp", "127.0.0.1", port)


@pytest.fixture(scope="session")
def edgenoded_url(host_port):
    """Base URL of edgenoded as reached from the test host."""
    return f"http://127.0.0.1:{host_port(EDGENODED_PORT)}"


@pytest.fixture(scope="session")
def ssh_port(host_port):
    """Host port that reaches the target's SSH server, once it answers."""
    port = host_port(SSH_PORT)
    helpers.wait_for_ssh_banner(port)
    return port
