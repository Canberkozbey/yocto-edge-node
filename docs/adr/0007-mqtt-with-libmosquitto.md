# ADR 0007: MQTT publishing with libmosquitto

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

The node publishes its readings to an MQTT broker, the usual way field
devices feed dashboards and data collection. A broker can be missing at
boot, restart, or drop off the network; the readings must keep flowing
when it comes back, and subscribers must be able to tell a node that is
gone from one that is quiet.

## Decision

- **libmosquitto** is the client, from meta-networking in
  meta-openembedded. kas adds meta-oe, meta-python and meta-networking,
  pinned with `kas lock` (ADR 0001). The image gets only the client library
  (`libmosquitto1`); the broker and command-line clients built by the same
  recipe stay out, and a bbappend drops websockets from the build
  (ADR 0003).
- **MQTT 3.1.1, plain at first.** TLS needed a CA, device certificates
  and provisioning. ADR 0011 added them: mutual TLS, with the node's
  identity on the data partition.
- **No broker in the image.** Which broker a node talks to belongs to the
  deployment, not the build; `mqtt_broker` is unset in every image, and
  the image tests check it. The tests run their own instance pointed at a
  broker they start.
- **Topics,** under `edgenode/<hostname>/`:
  - `sensors`: the `/sensors` JSON every `mqtt_interval` seconds (5 by
    default), QoS 0, retained, so a late subscriber gets the last readings
    at once;
  - `status`: `online` (retained) once connected; `offline` (retained) when
    the daemon stops, and as the last will, which the broker publishes when
    the connection drops without a goodbye.
- **A network thread of our own** runs libmosquitto's loop and reconnects
  with a delay from 1 s growing to 30 s. libmosquitto's own thread
  (`mosquitto_loop_start`) gives up for good on a name resolution failure,
  as at boot before the network is up. The main thread only publishes,
  which libmosquitto allows once told that threads are in use
  (`mosquitto_threaded_set`). DNS lookups and connection attempts never
  hold up the HTTP endpoints or the systemd watchdog.

## Testing

`tests/dev/test_mqtt.py` starts mosquitto on the test host and an extra
edgenoded on the target, reading a stand-in IIO device, so the same tests
run in QEMU (the broker on loopback, which QEMU's user network shows the
target as 10.0.2.2) and on the Raspberry Pi 5 (the build host's LAN
address). They check the payload, the retained values for a late
subscriber, a reading change, reconnection after a broker restart,
`offline` on a clean stop, and the last will after `kill -9`. Each check
fails when the behaviour it covers is removed from the daemon (checked
once by hand, by removing each in turn).

## Alternatives considered

- **An own minimal MQTT client:** no new layers, as with `sd_notify` and
  the KMS calls. But MQTT has more to get right (keep-alive, reconnects,
  QoS 1 acknowledgements), and TLS on top would mean driving OpenSSL by
  hand. libmosquitto is the reference client and small.
- **Paho MQTT C:** comparable, but not in the Yocto layers this build
  already trusts.
- **Per-channel topics** (`.../sensors/<name>`): simpler for some
  dashboards, but splits one consistent set of readings into messages
  that arrive separately.

## Consequences

- Three more layers to keep pinned and updated with the Yocto release.
- Plain MQTT has no authentication or encryption: acceptable on the lab
  network only. A deployment uses TLS (ADR 0011).
- Two threads in the daemon, sharing only libmosquitto's handle and a few
  atomic flags; checked with ThreadSanitizer.
