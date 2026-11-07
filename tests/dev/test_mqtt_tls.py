"""edgenoded publishes over MQTT with mutual TLS (docs/adr/0011).

A broker on the test host takes TLS only, and only from clients with a
certificate its CA issued. The CA and every certificate are made for the
test run (broker.py): the broker's, a dashboard's that subscribes, and the
node's. An extra edgenoded instance on the target gets its CA certificate,
certificate and key as files, as the site's configuration on /data gives
them to the service. Then each side refuses a peer it cannot verify, and a
node whose site configuration or identity is broken keeps running without
MQTT. The broker uses the same address and port as tests/dev/test_mqtt.py.
"""

import contextlib
import shlex
from types import SimpleNamespace

import pytest

import helpers
from broker import Broker, Pki, Subscriber

WORK = "/tmp/edgenoded-tls-test"
NAME = "tls"
HTTP_PORT = 8085


def target_option(target, name, default=None):
    try:
        return target.env.config.get_target_option(target.name, name)
    except KeyError:
        if default is None:
            pytest.fail(f"target option {name} missing from the labgrid environment")
        return default


def online(payload):
    return payload == "online"


def put_file(shell, path, text, mode="0600"):
    """Writes text to a file on the target, line by line over the console."""
    lines = " ".join(shlex.quote(line) for line in text.splitlines())
    shell.run_check(f"printf '%s\\n' {lines} > {path} && chmod {mode} {path}")


@pytest.fixture(scope="module")
def lab(target, shell, tmp_path_factory):
    """The test run's two CAs, the broker's address and the files on the
    target: the node's credentials from the fleet CA, and from another CA
    that the broker does not trust."""
    work = tmp_path_factory.mktemp("tls")
    listen = target_option(target, "mqtt_broker_listen")
    reach = target_option(target, "mqtt_broker_from_target")
    port = int(target_option(target, "mqtt_broker_port", 0)) or helpers.free_tcp_port()
    fleet, other = Pki(work, "fleet"), Pki(work, "other")
    lab = SimpleNamespace(
        work=work, listen=listen, reach=reach, port=port, fleet=fleet,
        broker=fleet.issue("broker", "server", [listen, reach]),
        dashboard=fleet.issue("dashboard", "client"),
        prefix=f"edgenode/{shell.run_check('hostname')[0]}",
    )
    shell.run_check(f"rm -rf {WORK} && mkdir -p {WORK}")
    files = {
        "fleet-ca.pem": fleet.ca,
        "other-ca.pem": other.ca,
    }
    for name, credentials in (("node", fleet.issue("node", "client")),
                              ("stranger", other.issue("stranger", "client"))):
        files[f"{name}.cert.pem"] = credentials.cert
        files[f"{name}.key.pem"] = credentials.key
    for name, path in files.items():
        put_file(shell, f"{WORK}/{name}", path.read_text())
    yield lab
    shell.run(f"rm -rf {WORK}")


@contextlib.contextmanager
def broker(lab, credentials=None):
    """The TLS broker with these credentials (the fleet's by default), and
    the dashboard subscribed to the node's topics."""
    process = Broker(lab.listen, lab.port, lab.work, tls=credentials or lab.broker)
    process.start()
    subscriber = None
    try:
        subscriber = Subscriber(process, f"{lab.prefix}/#", tls=lab.dashboard,
                                check_name=credentials is None)
        yield subscriber
    finally:
        if subscriber:
            subscriber.close()
        process.stop()


@contextlib.contextmanager
def node(shell, lab, settings, site=None):
    """edgenoded on the target with these MQTT settings; with site, they go
    into a site configuration file that the main one names instead."""
    lines = ["bind = 127.0.0.1", f"port = {HTTP_PORT}", "mqtt_interval = 1"]
    mqtt = [f"mqtt_broker = {lab.reach}", f"mqtt_port = {lab.port}", *settings]
    if site:
        put_file(shell, f"{WORK}/site.conf", "\n".join(mqtt + site), mode="0640")
        lines.append(f"site_config = {WORK}/site.conf")
    else:
        lines += mqtt
    helpers.start_edgenoded(shell, WORK, NAME, lines)
    try:
        yield
    finally:
        helpers.stop_edgenoded(shell, WORK, NAME)


def log(shell):
    return "\n".join(shell.run(f"cat {WORK}/{NAME}.log")[0])


def healthy(shell):
    """The node still serves its health while MQTT does not work."""
    return '"status":"ok"' in "".join(shell.run(
        f"wget -qO- http://127.0.0.1:{HTTP_PORT}/health 2>/dev/null")[0]).replace(" ", "")


NODE = [f"mqtt_ca = {WORK}/fleet-ca.pem", f"mqtt_cert = {WORK}/node.cert.pem",
        f"mqtt_key = {WORK}/node.key.pem"]


def test_publishes_over_mutual_tls(shell, lab):
    with broker(lab) as dashboard, node(shell, lab, NODE):
        assert dashboard.wait_for(f"{lab.prefix}/status", online), log(shell)
        assert dashboard.wait_for(f"{lab.prefix}/sensors"), log(shell)
        assert "over TLS with a client certificate" in log(shell)


def test_node_refuses_a_broker_it_cannot_verify(shell, lab):
    # The node trusts another CA than the one that issued the broker's
    # certificate.
    settings = [f"mqtt_ca = {WORK}/other-ca.pem", *NODE[1:]]
    with broker(lab) as dashboard, node(shell, lab, settings):
        assert not dashboard.wait_for(f"{lab.prefix}/status", online, timeout=10)
        # The log says which check failed, not only that one did.
        assert ("certificate verify failed "
                "(self-signed certificate in certificate chain)") in log(shell)
        assert healthy(shell)


def test_node_refuses_a_broker_certificate_for_another_name(shell, lab):
    # Issued by the right CA, but for an address the node did not connect to.
    elsewhere = lab.fleet.issue("elsewhere", "server", ["192.0.2.1"])
    with broker(lab, elsewhere) as dashboard, node(shell, lab, NODE):
        assert not dashboard.wait_for(f"{lab.prefix}/status", online, timeout=10)
        assert "certificate verify failed (IP address mismatch)" in log(shell)


def test_broker_refuses_a_node_without_a_certificate(shell, lab):
    with broker(lab) as dashboard, node(shell, lab, NODE[:1]):
        assert not dashboard.wait_for(f"{lab.prefix}/status", online, timeout=10)
        assert "Cannot reach MQTT broker" in log(shell)


def test_broker_refuses_a_node_certificate_from_another_ca(shell, lab):
    settings = [NODE[0], f"mqtt_cert = {WORK}/stranger.cert.pem",
                f"mqtt_key = {WORK}/stranger.key.pem"]
    with broker(lab) as dashboard, node(shell, lab, settings):
        assert not dashboard.wait_for(f"{lab.prefix}/status", online, timeout=10)
        assert "Cannot reach MQTT broker" in log(shell)


def test_site_configuration_sets_the_broker_and_identity(shell, lab):
    # As on the SD card: /etc/edgenoded.conf names the site's file on /data,
    # which holds what an image cannot know.
    with broker(lab) as dashboard, node(shell, lab, [], site=NODE):
        assert dashboard.wait_for(f"{lab.prefix}/status", online), log(shell)


def test_broken_site_configuration_is_ignored(shell, lab):
    # A certificate without its key: the daemon logs it and runs on the
    # product's settings, which name no broker.
    with broker(lab) as dashboard, node(shell, lab, [], site=NODE[:2]):
        assert not dashboard.wait_for(f"{lab.prefix}/status", online, timeout=10)
        assert "mqtt_cert and mqtt_key go together" in log(shell)
        assert healthy(shell)


def test_unreadable_identity_disables_mqtt_but_not_the_node(shell, lab):
    settings = [*NODE[:2], f"mqtt_key = {WORK}/missing.key.pem"]
    with broker(lab) as dashboard, node(shell, lab, settings):
        assert not dashboard.wait_for(f"{lab.prefix}/status", online, timeout=10)
        assert "MQTT disabled: cannot read" in log(shell)
        assert healthy(shell)
