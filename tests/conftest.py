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
def service_address(target, booted):
    """Returns a function that maps a target TCP port to the (host, port)
    the test host connects to.

    QEMU: the port is forwarded to a free port on 127.0.0.1.
    Hardware: the target's own address, from the NetworkService resource.
    """
    try:
        qemu = target.get_driver("QEMUDriver")
    except NoDriverFoundError:
        address = target.get_resource("NetworkService").address
        yield lambda port: (address, port)
        return

    forwarded = []

    def forward(guest_port):
        port = helpers.free_tcp_port()
        qemu.add_port_forward("tcp", "127.0.0.1", port, "", guest_port)
        forwarded.append(port)
        return "127.0.0.1", port

    yield forward
    for port in forwarded:
        qemu.remove_port_forward("tcp", "127.0.0.1", port)


@pytest.fixture(scope="session")
def edgenoded_url(service_address):
    """Base URL of edgenoded as reached from the test host."""
    host, port = service_address(EDGENODED_PORT)
    return f"http://{host}:{port}"


@pytest.fixture(scope="session")
def ssh_address(service_address):
    """(host, port) of the target's SSH server, once it answers."""
    host, port = service_address(SSH_PORT)
    helpers.wait_for_ssh_banner(host, port)
    return host, port
