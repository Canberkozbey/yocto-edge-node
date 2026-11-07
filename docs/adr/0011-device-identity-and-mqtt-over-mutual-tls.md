# ADR 0011: Device identity on the data partition, MQTT over mutual TLS

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Until now edgenoded published over plain MQTT (ADR 0007). On a shared
network, that has two problems:

- Anyone on the network can read the readings.
- A node cannot tell the real broker from an impostor, and the broker
  cannot tell a node from anyone else using its name.

TLS with certificates on both sides fixes this, but it needs a key and a
certificate per node, and the image is the wrong place for them:

- Every node runs the same image.
- Releases publish the image (ADR 0010).
- The root filesystem is read-only and verified (ADR 0009).

The same goes for which broker a node uses: that belongs to the site, not
to the build. One more piece of identity was missing as well: with a
read-only root, sshd made new host keys at every boot, so clients saw a new
host key after each reboot.

## Decision

- **MQTT over TLS, with a certificate on each side.** These settings are
  optional:
  - `mqtt_ca`: the CA certificate that the broker's certificate must chain
    to. Setting it turns TLS on.
  - `mqtt_cert` and `mqtt_key`: the node's own certificate and key. They go
    together and need `mqtt_ca`.

  libmosquitto checks the broker's certificate and that it names the
  broker as configured, host name or IP address. It accepts TLS 1.2 or
  later. With TLS on, the port defaults to 8883. libmosquitto is built with
  TLS again; websockets stay off.
- **The site's settings live on the data partition, which updates keep.**
  `/etc/edgenoded.conf` names a second file, `site_config =
  /data/edgenode/edgenoded.conf`. edgenoded reads it after the image's own
  file, and its settings win. That is where the broker and the node's
  identity go, next to the key and the certificates. The image sets neither
  a broker nor an identity, and the image tests check that.
- **Only the daemon's group can read them.** edgenoded runs as a dynamic,
  unprivileged user (ADR 0003) with one supplementary group,
  `edgenode-identity`. `edgenoded-site.service` runs before the daemon at
  every boot that has the data partition:
  - It makes `/data/edgenode` and gives every directory and regular file in
    it to `root:edgenode-identity`.
  - Directories get mode 0750 and files 0640.
  - It leaves symbolic links alone, so the rights cannot reach a file
    outside the directory.

  The setup runs again at every boot for two reasons: the group's number
  can differ between two images, while the partition outlives both, and
  provisioning may leave a key readable by root only.
- **A broken identity does not stop the node.** The node keeps serving its
  health and readings, so an update can still be committed (ADR 0008) and
  the site can fix its settings:
  - If the site's file has an error, edgenoded logs it, ignores the file
    and runs on the image's settings.
  - If a file the settings name cannot be read, MQTT is off and the reason
    is logged.
  - If a TLS handshake fails, edgenoded logs OpenSSL's reason once and
    retries with the usual backoff. When the broker's certificate fails
    verification, OpenSSL's reason is only "certificate verify failed", so
    edgenoded adds the check that failed from the connection's verify
    result, such as "IP address mismatch" for a certificate issued for
    another address or "self-signed certificate in certificate chain" for
    one from another CA. libmosquitto 2.1 leaves the name check to OpenSSL
    and does not log it itself, so edgenoded links OpenSSL for this.
- **The SSH host keys stay too.** On boots from the card, the generator
  (ADR 0008) adds `run-ssh.mount`, which bind-mounts `/data/ssh` on
  `/run/ssh`, where sshd keeps its keys on a read-only root.
  `sshdgenkeys.service` makes the keys there on the first boot and finds
  them on every boot after that. Network boots still make new keys in
  `/run`.

## Testing

- **`tests/dev/test_mqtt_tls.py`, on every machine.** It runs a broker on
  the test host that accepts only TLS from clients with a certificate from
  its CA. The CA and all the certificates are made for each run
  (`tests/broker.py`). The tests check:
  - publishing over mutual TLS;
  - the node refuses a broker whose certificate comes from another CA, and
    one whose certificate names another address;
  - the broker refuses a node without a certificate, and one with a
    certificate from another CA;
  - the site's file sets the broker and the identity;
  - a broken site file, or an unreadable key, leaves the node running
    without MQTT.
- **`tests/update`, on the Raspberry Pi 5.** On the card's first boot:
  - the SSH host keys are on `/data`;
  - a site's configuration and a node identity provisioned there, with the
    key readable by root only, bring edgenoded online over mutual TLS;
  - the directory and the key end up `root:edgenode-identity`, 0750 and
    0640.

  After the update to the other slot, the node has the same SSH host keys
  and connects with the same identity.
- **`tests/image`** checks:
  - the daemon's group and unit;
  - the site unit and the generator's SSH key mount;
  - the image names the site's file, but neither a broker nor an identity.

## Alternatives considered

- **systemd's `LoadCredential=`:** it hands the key to the service without
  any group, and is the right tool for a secret that is always there. A
  node that is not provisioned yet has no key, though, and then the service
  fails to start. The commit service (ADR 0008) would read that as a broken
  release and roll back.
- **Keys in the image:** one key shared by every node, published with
  every release.
- **A username and password for the broker:** a shared secret in a
  configuration file, sent over the connection, and it says nothing about
  the broker.
- **A secure element or TPM for the key:** the right place for it, since
  the key could not be copied off the card. The Pi 5 has neither. A HAT
  with one, through PKCS#11 (libmosquitto can take a key through an
  OpenSSL engine), is the way there.
- **Generating the key on the node and sending a CSR out:** better than
  making it elsewhere, since the key never leaves the node. It needs an
  enrolment service (EST, ACME, a fleet manager). Here the site provisions
  the files (docs/provisioning.md), and the directory layout does not
  change if that becomes enrolment.

## Consequences

- **The key is as safe as the card.** Anyone who has the SD card can copy
  the identity: `/data` is not encrypted. A secure element, or an encrypted
  data partition unlocked through the Pi 5's OTP, would change that.
  Neither is done on this lab board.
- **Certificates expire, and renewal is manual.** The site replaces the
  files and restarts edgenoded. Revocation is the broker's job (a CRL),
  and nothing sets that up here.
- **Topics follow the host name, not the certificate.** The broker maps a
  certificate to a client (`use_identity_as_username` in mosquitto). An ACL
  per node, so that a node may write only its own topics, is configured on
  the broker.
- **Every boot from the card touches the site directory.** It costs a few
  `chown` and `chmod` calls, and corrects mistakes instead of failing on
  them.
