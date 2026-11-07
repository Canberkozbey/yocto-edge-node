"""Helpers shared by the test directories (dev, prod, image)."""

import bz2
import functools
import hashlib
import os
import shlex
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

import paramiko

MACHINE = os.environ.get("LG_MACHINE", "qemuarm64")
# What the build under test was asked to make, as CI sets it (the image
# tests read the deploy directory, which does not record it):
#   EDGENODE_CVE_CHECK=1  the build ran the CVE check (kas/cve-check.yml)
#   EDGENODE_RELEASE=1    release build: no development bundle
#                         (kas/release.yml)
#   EDGENODE_CA_CERT      the CA certificate the build's images must trust
CVE_CHECK = os.environ.get("EDGENODE_CVE_CHECK") == "1"
RELEASE = os.environ.get("EDGENODE_RELEASE") == "1"
CA_CERT = os.environ.get("EDGENODE_CA_CERT")


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


def wait_until(condition, timeout=15, interval=0.5):
    """Calls condition() until it returns something true; returns that, or
    the last (false) result once timeout seconds have passed."""
    deadline = time.monotonic() + timeout
    while True:
        result = condition()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(interval)


def drm_debugfs(shell, connector):
    """The DRM debugfs directory of the card with this connector (a name, or
    a glob such as SPI-*, as in /sys/class/drm/card<N>-<connector>)."""
    out = shell.run_check(f"ls -1d /sys/class/drm/card*-{connector}")
    assert len(out) == 1, f"expected one {connector} connector, found: {out}"
    card = out[0].rsplit("/", 1)[1].split("-", 1)[0]
    return f"/sys/kernel/debug/dri/{card.removeprefix('card')}"


def screen_owners(shell, debugfs):
    """Processes whose framebuffers are on screen: debugfs prints "allocated
    by = <comm>" under each plane that shows one."""
    state = shell.run_check(f"cat {debugfs}/state")
    return [line.split("=", 1)[1].strip() for line in state if "allocated by" in line]


def start_edgenoded(shell, work, name, lines):
    """Starts an extra edgenoded on the target, configured by these lines,
    with its files in work: <name>.conf, .log and .pid."""
    conf = f"{work}/{name}.conf"
    shell.run_check(f"printf '%s\\n' {' '.join(shlex.quote(line) for line in lines)} > {conf}")
    # A subshell keeps the process out of the shell's job table.
    shell.run_check(f"(setsid edgenoded --config {conf} </dev/null "
                    f">{work}/{name}.log 2>&1 & echo $! > {work}/{name}.pid)")


def stop_edgenoded(shell, work, name, signal="TERM"):
    shell.run(f"kill -{signal} $(cat {work}/{name}.pid) 2>/dev/null; true")


def wait_for_ssh_banner(host, port, timeout=120):
    """Waits until an SSH server answers on host:port."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                if s.recv(64).startswith(b"SSH-"):
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"no SSH server on {host}:{port}")
        time.sleep(2)


def ssh_accepts_empty_password(host, port, user="root"):
    """True if the SSH server logs `user` in with an empty password.

    Tries the "none" method (OpenSSH accepts it for empty passwords when
    PermitEmptyPasswords is set) and then an empty password.
    """
    with socket.create_connection((host, port), timeout=10) as sock:
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


class Image:
    """A built image in the deploy directory, inspected without booting it."""

    def __init__(self, deploy_dir, name, machine=MACHINE):
        stem = f"{name}-{machine}.rootfs"
        deploy_dir = Path(deploy_dir)
        self.ext4 = deploy_dir / f"{stem}.ext4"
        self.manifest = deploy_dir / f"{stem}.manifest"
        # tmp/deploy/images/<machine>/ -> tmp/deploy/licenses/<machine arch>/
        self._licenses_dir = deploy_dir.parent.parent / "licenses" / machine.replace("-", "_")

    @property
    def license_manifest(self):
        """The license manifest written together with this image's rootfs.

        do_rootfs writes it to licenses/<arch>/<image name>/, where the image
        name ends in the build's timestamp, and writes the package manifest
        in the deploy directory under the same name. So the deployed
        manifest's link gives the directory. oe-core's own link in the
        licenses directory is not used: do_populate_lic_deploy makes it, and
        when that task runs in a later build than do_rootfs, it points at a
        new directory without license.manifest.
        """
        manifest = self.manifest
        if manifest.is_symlink():
            manifest = Path(os.readlink(manifest))
        image_name = manifest.name.removesuffix(".manifest")
        return self._licenses_dir / image_name / "license.manifest"

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

    def read_bytes(self, path):
        """Reads a file from the ext4 image with debugfs; no mount, no root."""
        if not self.ext4.is_file():
            # debugfs reports a missing image on stderr but still exits 0,
            # which would surface later as a confusing "not found" result.
            raise FileNotFoundError(f"image not built: {self.ext4}")
        debugfs = shutil.which("debugfs") or "/usr/sbin/debugfs"
        result = subprocess.run(
            [debugfs, "-R", f"cat {path}", str(self.ext4)],
            capture_output=True, check=True,
        )
        if b"File not found" in result.stderr:
            raise FileNotFoundError(f"{path} not in {self.ext4.name}")
        return result.stdout

    def read_file(self, path):
        """Like read_bytes, for text files."""
        return self.read_bytes(path).decode()

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


@dataclass
class Partition:
    number: int
    name: str
    type: str
    start: int  # bytes
    size: int  # bytes


def gpt_partitions(wic_bz2):
    """The partitions of a compressed disk image's primary GPT, read without
    decompressing more than its first sectors."""
    with bz2.open(wic_bz2) as f:
        head = f.read(64 * 1024)
    header = head[512:1024]
    if header[:8] != b"EFI PART":
        raise ValueError(f"{wic_bz2.name}: no GPT")
    entries_lba, count, entry_size = struct.unpack_from("<QII", header, 72)
    partitions = []
    for i in range(count):
        entry = head[entries_lba * 512 + i * entry_size:][:entry_size]
        type_guid = uuid.UUID(bytes_le=entry[:16])
        if type_guid.int == 0:
            continue
        first, last = struct.unpack_from("<QQ", entry, 32)
        name = entry[56:128].decode("utf-16-le").rstrip("\0")
        partitions.append(Partition(i + 1, name, str(type_guid).upper(),
                                    first * 512, (last - first + 1) * 512))
    return partitions


def fat_read(image, path):
    """Reads a file from a FAT filesystem image with mtools; no mount."""
    return subprocess.run(
        ["mtype", "-i", str(image), f"::{path}"], capture_output=True, check=True,
        env={**os.environ, "MTOOLS_SKIP_CHECK": "1"},
    ).stdout


def fat_files(image):
    """Paths of all files in a FAT filesystem image."""
    out = subprocess.run(
        ["mdir", "-/", "-b", "-i", str(image), "::"], capture_output=True, check=True,
        text=True, env={**os.environ, "MTOOLS_SKIP_CHECK": "1"},
    ).stdout
    return {line.removeprefix("::/") for line in out.splitlines()
            if line and not line.endswith("/")}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_manifest(bundle):
    """{section: {key: value}} of a RAUC bundle's manifest, read from its
    squashfs with unsquashfs (squashfs-tools)."""
    text = subprocess.run(
        ["unsquashfs", "-cat", str(bundle), "manifest.raucm"],
        capture_output=True, check=True, text=True,
    ).stdout
    return parse_manifest(text)


def bundle_signature(bundle, keyring, work):
    """Verifies a RAUC bundle's signature against a keyring, as RAUC does
    before it installs, with openssl. Returns the signed manifest and the
    signer's certificate (PEM); raises CalledProcessError if it does not
    verify.

    The bundle ends with its CMS signature and the signature's size (64-bit
    big endian). In the verity format the signature carries the manifest,
    with the root hash of the squashfs before it.
    """
    work = Path(work)
    with open(bundle, "rb") as f:
        f.seek(-8, os.SEEK_END)
        (size,) = struct.unpack(">Q", f.read(8))
        f.seek(-8 - size, os.SEEK_END)
        (work / "signature.der").write_bytes(f.read(size))
    # -purpose any: openssl 3.0 has no code signing purpose, so the caller
    # checks the signer's extended key usage itself.
    subprocess.run(
        ["openssl", "cms", "-verify", "-inform", "DER",
         "-in", str(work / "signature.der"), "-CAfile", str(keyring),
         "-purpose", "any", "-signer", str(work / "signer.pem"),
         "-out", str(work / "manifest.raucm")],
        capture_output=True, check=True,
    )
    return (parse_manifest((work / "manifest.raucm").read_text()),
            (work / "signer.pem").read_text())


def parse_manifest(text):
    """{section: {key: value}} of a RAUC manifest's text."""
    sections, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif "=" in line and current is not None:
            key, _, value = line.partition("=")
            current[key] = value
    return sections


def bmap_ranges(bmap):
    """(checksum type, [(offset, length, checksum)]) of a bmaptool block
    map: the ranges of an image that hold data; the rest is unused. The last
    range ends with the image, which need not be a whole number of blocks."""
    root = ElementTree.parse(bmap).getroot()
    block_size = int(root.findtext("BlockSize"))
    image_size = int(root.findtext("ImageSize"))
    checksum_type = (root.findtext("ChecksumType") or "sha1").strip()
    ranges = []
    for element in root.iter("Range"):
        first, _, last = element.text.strip().partition("-")
        last = last or first
        offset = int(first) * block_size
        length = min((int(last) - int(first) + 1) * block_size, image_size - offset)
        ranges.append((offset, length, element.get("chksum")))
    return checksum_type, ranges


class TargetSSH:
    """SSH to the development image as root, which logs in without a
    password; for commands that stream data in or out, which the serial
    console cannot carry."""

    def __init__(self, host, port=22):
        self._sock = socket.create_connection((host, port), timeout=10)
        self._transport = paramiko.Transport(self._sock)
        self._transport.start_client(timeout=10)
        try:
            self._transport.auth_none("root")
        except paramiko.AuthenticationException:
            self._transport.auth_password("root", "")
        self._transport.set_keepalive(10)

    def close(self):
        self._transport.close()

    def run(self, command, stdin=None, timeout=600, stdout_sink=None):
        """Runs command; `stdin` is an iterable of bytes chunks. Returns
        (exit status, stdout bytes, stderr bytes); with `stdout_sink`, a
        function, stdout goes to it in chunks instead."""
        channel = self._transport.open_session(timeout=10)
        channel.settimeout(timeout)
        channel.exec_command(command)
        if stdin is not None:
            for chunk in stdin:
                channel.sendall(chunk)
        channel.shutdown_write()
        stdout = []
        while chunk := channel.recv(1 << 16):
            (stdout_sink or stdout.append)(chunk)
        stderr = channel.makefile_stderr("rb").read()
        status = channel.recv_exit_status()
        channel.close()
        return status, b"".join(stdout), stderr

    def run_check(self, command, stdin=None, timeout=600, stdout_sink=None):
        status, stdout, stderr = self.run(command, stdin, timeout, stdout_sink)
        if status != 0:
            raise RuntimeError(f"{command!r} failed ({status}): {stderr.decode(errors='replace')}")
        return stdout

    def put(self, path, remote_path):
        """Copies a local file to the target."""
        def chunks():
            with open(path, "rb") as f:
                yield from iter(lambda: f.read(1 << 20), b"")
        self.run_check(f"cat > {shlex.quote(remote_path)}", stdin=chunks())

    def write_image(self, wic_bz2, bmap, device):
        """Writes a compressed disk image to a block device of the target:
        only the ranges the block map lists, each read back from the device
        and checked against the block map's checksum."""
        checksum_type, ranges = bmap_ranges(bmap)
        with bz2.open(wic_bz2) as image:
            for offset, length, _ in ranges:
                image.seek(offset)

                def chunks(remaining=length):
                    while remaining:
                        chunk = image.read(min(remaining, 1 << 20))
                        if not chunk:
                            raise EOFError("image ends before its block map does")
                        remaining -= len(chunk)
                        yield chunk

                # busybox dd writes each read as it came; without count=,
                # short reads from the pipe lose nothing.
                self.run_check(f"dd of={device} bs=4096 seek={offset // 4096} 2>/dev/null",
                               stdin=chunks())
        self.run_check("sync && echo 3 > /proc/sys/vm/drop_caches")
        for offset, length, checksum in ranges:
            # Whole 4 KiB blocks, except for a last range that ends in a
            # partial block, which the image's sectors still divide.
            block = 4096 if length % 4096 == 0 else 512
            digest = hashlib.new(checksum_type)
            self.run_check(f"dd if={device} bs={block} skip={offset // block} "
                           f"count={length // block} 2>/dev/null", stdout_sink=digest.update)
            assert not checksum or digest.hexdigest() == checksum, \
                f"{device}: bytes {offset}+{length} read back differently"
