"""An MQTT broker and subscriber on the test host, and the certificates for
MQTT over TLS: a mosquitto process, a paho client, and a small PKI made
with openssl at test time (tests/dev/test_mqtt*.py, tests/update).

Host tools: mosquitto and openssl (docs/lab.md).
"""

import ipaddress
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest


@dataclass(frozen=True)
class Credentials:
    """A certificate, its key, and the CA certificate that checks its peer."""

    ca: Path
    cert: Path
    key: Path


class Pki:
    """A CA that issues certificates for a test run: EC P-256, valid for a
    day, under workdir. Each instance is a separate CA."""

    def __init__(self, workdir, name):
        self.dir = Path(workdir) / name
        self.dir.mkdir(parents=True)
        self.name = name
        self.ca = self.dir / "ca.cert.pem"
        self._ca_key = self.dir / "ca.key.pem"
        self._openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                      "-nodes", "-keyout", self._ca_key, "-out", self.ca, "-days", "1",
                      "-subj", f"/CN={name} test CA",
                      "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                      "-addext", "keyUsage=critical,keyCertSign,cRLSign")

    @staticmethod
    def _openssl(*args):
        subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)

    def issue(self, name, usage, addresses=(), trust=None):
        """Credentials for name: usage "server" (with the addresses, IPs or
        host names, it is reached at) or "client". trust is the CA
        certificate the holder checks its peer with; this CA by default."""
        key, csr = self.dir / f"{name}.key.pem", self.dir / f"{name}.csr"
        cert, ext = self.dir / f"{name}.cert.pem", self.dir / f"{name}.ext"
        extensions = [
            "basicConstraints=critical,CA:FALSE",
            "keyUsage=critical,digitalSignature",
            f"extendedKeyUsage={'serverAuth' if usage == 'server' else 'clientAuth'}",
        ]
        names = []
        for address in addresses:
            try:
                names.append(f"IP:{ipaddress.ip_address(address)}")
            except ValueError:
                names.append(f"DNS:{address}")
        if names:
            extensions.append("subjectAltName=" + ",".join(dict.fromkeys(names)))
        ext.write_text("\n".join(extensions) + "\n")
        self._openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                      "-nodes", "-keyout", key, "-out", csr, "-subj", f"/CN={name}")
        self._openssl("x509", "-req", "-in", csr, "-CA", self.ca, "-CAkey", self._ca_key,
                      "-CAcreateserial", "-days", "1", "-extfile", ext, "-out", cert)
        return Credentials(trust or self.ca, cert, key)


class Broker:
    """mosquitto on the test host; stop() and start() keep the port. With
    tls (the broker's Credentials), it takes TLS only, and only from clients
    with a certificate its CA issued."""

    def __init__(self, listen, port, workdir, tls=None):
        self.listen, self.port, self.tls = listen, port, tls
        workdir = Path(workdir)
        self.conf = workdir / "mosquitto.conf"
        lines = [f"listener {port} {listen}", "allow_anonymous true", "persistence false"]
        if tls:
            lines += [f"cafile {tls.ca}", f"certfile {tls.cert}", f"keyfile {tls.key}",
                      "require_certificate true", "use_identity_as_username true"]
        self.conf.write_text("\n".join(lines) + "\n")
        self.log = workdir / "mosquitto.log"
        self.process = None

    def start(self):
        binary = shutil.which("mosquitto") or "/usr/sbin/mosquitto"
        if not Path(binary).is_file():
            pytest.fail("mosquitto is not installed on the test host (docs/lab.md)")
        with open(self.log, "a") as log:
            self.process = subprocess.Popen([binary, "-c", str(self.conf)],
                                            stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection((self.listen, self.port), timeout=1).close()
                return
            except OSError:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"mosquitto did not start:\n{self.log.read_text()}")
                time.sleep(0.1)

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)

    def log_text(self):
        return self.log.read_text() if self.log.exists() else ""


class Subscriber:
    """Collects the messages under a topic filter; subscribes again after
    a reconnect, so it outlives a broker restart. With tls (Credentials),
    connects over TLS with that client certificate; check_name=False skips
    checking that the broker's certificate names its address."""

    def __init__(self, broker, topic_filter, tls=None, check_name=True):
        self.messages = []  # (topic, payload, retained)
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if tls:
            self.client.tls_set(ca_certs=str(tls.ca), certfile=str(tls.cert),
                                keyfile=str(tls.key))
            # Still checks the chain; only for a broker certificate made for
            # another name on purpose.
            self.client.tls_insecure_set(not check_name)
        self.client.on_connect = lambda client, *_: client.subscribe(topic_filter)
        self.client.on_message = self._on_message
        self.client.connect(broker.listen, broker.port)
        self.client.loop_start()

    def _on_message(self, client, userdata, message):
        with self.lock:
            self.messages.append((message.topic, message.payload.decode(), message.retain))

    def wait_for(self, topic, condition=lambda payload: True, timeout=30, after=0):
        """The first message on topic, from index `after` on, whose payload
        meets condition, as (index, payload, retained); None after timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                messages = list(enumerate(self.messages))[after:]
            for index, (t, payload, retained) in messages:
                if t == topic and condition(payload):
                    return index, payload, retained
            time.sleep(0.1)
        return None

    def count(self):
        with self.lock:
            return len(self.messages)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
