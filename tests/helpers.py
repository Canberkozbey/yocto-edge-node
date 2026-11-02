"""Helpers shared by the test directories (dev, prod, image)."""

import functools
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import paramiko

MACHINE = os.environ.get("LG_MACHINE", "qemuarm64")


def free_tcp_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(url, timeout=10):
    """Returns (status, body); HTTP errors are results, not exceptions."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def wait_for_http(url, timeout=120):
    """Like http_get, but retries while the service is not reachable yet."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return http_get(url)
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def wait_for_ssh_banner(port, timeout=120):
    """Waits until an SSH server answers on 127.0.0.1:port."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                if s.recv(64).startswith(b"SSH-"):
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"no SSH server on port {port}")
        time.sleep(2)


def ssh_accepts_empty_password(port, user="root"):
    """True if the SSH server logs `user` in with an empty password.

    Tries the "none" method (OpenSSH accepts it for empty passwords when
    PermitEmptyPasswords is set) and then an empty password.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=10)
            for attempt in (lambda: transport.auth_none(user),
                            lambda: transport.auth_password(user, "")):
                try:
                    attempt()
                except paramiko.AuthenticationException:
                    pass
                if transport.is_authenticated():
                    return True
            return False
        finally:
            transport.close()


def _license_dir(licenses_dir, link_name):
    """The image's license directory, found through its link name.

    oe-core links <link name> to the dated directory with an absolute path.
    In a kas-container build that path exists only inside the container
    (/work/...), so follow the link by name within licenses_dir instead.
    """
    link = licenses_dir / link_name
    if link.is_symlink():
        return licenses_dir / Path(os.readlink(link)).name
    return link


class Image:
    """A built image in the deploy directory, inspected without booting it."""

    def __init__(self, deploy_dir, name, machine=MACHINE):
        stem = f"{name}-{machine}.rootfs"
        deploy_dir = Path(deploy_dir)
        self.ext4 = deploy_dir / f"{stem}.ext4"
        self.manifest = deploy_dir / f"{stem}.manifest"
        # tmp/deploy/images/<machine>/ -> tmp/deploy/licenses/<machine arch>/
        licenses_dir = deploy_dir.parent.parent / "licenses" / machine.replace("-", "_")
        self.license_manifest = _license_dir(licenses_dir, stem) / "license.manifest"

    @functools.cached_property
    def packages(self):
        """Installed package names, from the image manifest."""
        lines = self.manifest.read_text().splitlines()
        return {line.split()[0] for line in lines if line.strip()}

    @functools.cached_property
    def licenses(self):
        """{package: license expression} from the image's license manifest.

        For dual-licensed packages ("A | B") the build records the branch it
        chose under the image's license policy.
        """
        licenses = {}
        package = None
        for line in self.license_manifest.read_text().splitlines():
            key, _, value = line.partition(": ")
            if key == "PACKAGE NAME":
                package = value.strip()
            elif key == "LICENSE" and package:
                licenses[package] = value.strip()
                package = None
        return licenses

    def read_file(self, path):
        """Reads a file from the ext4 image with debugfs; no mount, no root."""
        debugfs = shutil.which("debugfs") or "/usr/sbin/debugfs"
        result = subprocess.run(
            [debugfs, "-R", f"cat {path}", str(self.ext4)],
            capture_output=True, text=True, check=True,
        )
        if "File not found" in result.stderr:
            raise FileNotFoundError(f"{path} not in {self.ext4.name}")
        return result.stdout

    def shadow_entry(self, user):
        for line in self.read_file("/etc/shadow").splitlines():
            fields = line.split(":")
            if fields[0] == user:
                return fields
        raise KeyError(f"{user} not in /etc/shadow")

    def sshd_settings(self):
        """Active sshd_config settings, keys lowercased (sshd ignores case)."""
        settings = {}
        for line in self.read_file("/etc/ssh/sshd_config").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition(" ")
                settings.setdefault(key.lower(), value.strip())
        return settings
