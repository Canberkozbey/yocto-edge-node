"""edgenoded publishes the readings over MQTT.

The tests start a mosquitto broker on the test host and an extra edgenoded
instance on the target, which reads a stand-in IIO device and publishes to
that broker. The images set no broker (tests/image checks it), so the
service itself publishes nothing. Where the broker listens, and the address
the target reaches it at, are target options in the labgrid environment
(tests/env/*-dev.yaml).
"""

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

import helpers

WORK = "/tmp/edgenoded-mqtt-test"
FAKE_IIO = f"{WORK}/adc/iio:device0"
NAME = "mqtt"


class Broker:
    """mosquitto on the test host; stop() and start() keep the port."""

    def __init__(self, listen, port, workdir):
        self.listen, self.port = listen, port
        self.conf = workdir / "mosquitto.conf"
        self.conf.write_text(
            f"listener {port} {listen}\nallow_anonymous true\npersistence false\n"
        )
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


class Subscriber:
    """Collects the messages under a topic filter; subscribes again after
    a reconnect, so it outlives a broker restart."""

    def __init__(self, broker, topic_filter):
        self.messages = []  # (topic, payload, retained)
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
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


def target_option(target, name, default=None):
    try:
        return target.env.config.get_target_option(target.name, name)
    except KeyError:
        if default is None:
            pytest.fail(f"target option {name} missing from the labgrid environment")
        return default


def set_raw(shell, value):
    shell.run_check(f"echo {value} > '{FAKE_IIO}/in_voltage0_raw'")


def readings(payload):
    return json.loads(payload)["sensors"]


@pytest.fixture(scope="module")
def broker(target, tmp_path_factory):
    listen = target_option(target, "mqtt_broker_listen")
    port = int(target_option(target, "mqtt_broker_port", 0)) or helpers.free_tcp_port()
    broker = Broker(listen, port, tmp_path_factory.mktemp("mqtt"))
    broker.start()
    yield broker
    broker.stop()


@pytest.fixture(scope="module")
def prefix(shell):
    return f"edgenode/{shell.run_check('hostname')[0]}"


@pytest.fixture(scope="module")
def subscriber(broker, prefix):
    subscriber = Subscriber(broker, f"{prefix}/#")
    yield subscriber
    subscriber.close()


@pytest.fixture(scope="module")
def config(target, broker):
    return [
        "bind = 127.0.0.1",
        "port = 8084",
        f"adc_device = {WORK}/adc",
        "channel.knob = 0",
        f"mqtt_broker = {target_option(target, 'mqtt_broker_from_target')}",
        f"mqtt_port = {broker.port}",
        "mqtt_interval = 1",
    ]


@pytest.fixture(scope="module")
def instance(shell, config, subscriber):
    shell.run_check(f"rm -rf {WORK} && mkdir -p '{FAKE_IIO}'")
    set_raw(shell, 16520)
    shell.run_check(f"echo 0.125000000 > '{FAKE_IIO}/in_voltage0_scale'")
    helpers.start_edgenoded(shell, WORK, NAME, config)
    yield
    helpers.stop_edgenoded(shell, WORK, NAME)
    shell.run(f"rm -rf {WORK}")


def log(shell):
    return "\n".join(shell.run(f"cat {WORK}/{NAME}.log")[0])


def test_publishes_status_and_readings(shell, prefix, subscriber, instance):
    assert subscriber.wait_for(f"{prefix}/status", lambda p: p == "online"), log(shell)
    message = subscriber.wait_for(f"{prefix}/sensors")
    assert message, log(shell)
    assert readings(message[1]) == {"knob": {"channel": 0, "raw": 16520, "millivolts": 2065.0}}


def test_late_subscriber_gets_the_last_values(broker, prefix, instance):
    # Both topics are retained, so a dashboard that connects later is not
    # left empty until the next reading.
    late = Subscriber(broker, f"{prefix}/#")
    try:
        for topic in ("status", "sensors"):
            message = late.wait_for(f"{prefix}/{topic}", lambda p: True, timeout=10)
            assert message and message[2], f"{topic}: no retained message"
    finally:
        late.close()


def test_follows_reading_changes(shell, prefix, subscriber, instance):
    set_raw(shell, 8000)
    assert subscriber.wait_for(
        f"{prefix}/sensors", lambda p: readings(p)["knob"]["millivolts"] == 1000.0)


def test_reconnects_after_broker_restart(shell, broker, prefix, subscriber, instance):
    broker.stop()
    time.sleep(2)
    after = subscriber.count()
    broker.start()
    # The broker kept nothing (no persistence): "online" and readings again
    # mean edgenoded connected anew. Its retry delay starts at 1 s.
    assert subscriber.wait_for(f"{prefix}/status", lambda p: p == "online", after=after), \
        log(shell)
    assert subscriber.wait_for(f"{prefix}/sensors", after=after)


def test_says_offline_when_it_stops(shell, prefix, subscriber, instance):
    after = subscriber.count()
    helpers.stop_edgenoded(shell, WORK, NAME)
    assert subscriber.wait_for(f"{prefix}/status", lambda p: p == "offline", after=after), \
        log(shell)


def test_last_will_when_it_dies(shell, config, prefix, subscriber, instance):
    # Killed, it cannot say goodbye: the broker publishes its last will
    # once the connection drops.
    after = subscriber.count()
    helpers.start_edgenoded(shell, WORK, NAME, config)
    online = subscriber.wait_for(f"{prefix}/status", lambda p: p == "online", after=after)
    assert online, log(shell)
    helpers.stop_edgenoded(shell, WORK, NAME, signal="KILL")
    assert subscriber.wait_for(f"{prefix}/status", lambda p: p == "offline", after=online[0] + 1)
