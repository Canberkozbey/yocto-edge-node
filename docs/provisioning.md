# Provisioning a node

What an image cannot carry goes on the node's data partition, which updates
keep. Each node needs:

- the site's configuration, with the MQTT broker;
- the node's MQTT identity, a key and a certificate;
- the CA certificate that the broker's certificate must chain to.

The SSH host keys live there too; the node makes them on its first boot.
The reasons are in [ADR 0011](adr/0011-device-identity-and-mqtt-over-mutual-tls.md).

## What goes where

| Path on the node | What it is |
| --- | --- |
| `/data/edgenode/edgenoded.conf` | The site's configuration: read after `/etc/edgenoded.conf`, and its settings win |
| `/data/edgenode/mqtt/ca.cert.pem` | The CA certificate that the broker's certificate must chain to |
| `/data/edgenode/mqtt/node.cert.pem` | The node's certificate, from the CA that the broker trusts for clients |
| `/data/edgenode/mqtt/node.key.pem` | The node's key |
| `/data/ssh/` | The SSH host keys, made on the first boot from the card |

At every boot, `edgenoded-site.service` gives `/data/edgenode` to
`root:edgenode-identity`, directories 0750 and files 0640, so only
edgenoded's group can read it. File modes from provisioning do not matter.

The site's configuration uses the keys of `/etc/edgenoded.conf`, for
example:

```ini
mqtt_broker = 192.168.1.173
mqtt_ca = /data/edgenode/mqtt/ca.cert.pem
mqtt_cert = /data/edgenode/mqtt/node.cert.pem
mqtt_key = /data/edgenode/mqtt/node.key.pem
```

With `mqtt_ca`, the port defaults to 8883. The broker's certificate must
name the address in `mqtt_broker`, here the build host's.

## In the lab

The commands below set up a fleet CA, a broker on the build host and the
identity of the Raspberry Pi 5. They use openssl and mosquitto from Ubuntu
on the build host.

### Once: a fleet CA

On a machine you trust, kept apart from the update signing keys:

```sh
mkdir -p ~/yocto/fleet-ca && cd ~/yocto/fleet-ca
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout ca.key.pem -out ca.cert.pem -days 3650 \
    -subj "/CN=Edge Node lab fleet CA" \
    -addext basicConstraints=critical,CA:TRUE,pathlen:0 \
    -addext keyUsage=critical,keyCertSign,cRLSign
chmod 0600 ca.key.pem
```

### Once: the broker

A certificate for the build host's address, and a TLS listener that takes
only clients with a certificate from the fleet CA:

```sh
cd ~/yocto/fleet-ca
openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout broker.key.pem -out broker.csr -subj "/CN=broker"
printf '%s\n' "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" \
    "extendedKeyUsage=serverAuth" "subjectAltName=IP:192.168.1.173" > broker.ext
openssl x509 -req -in broker.csr -CA ca.cert.pem -CAkey ca.key.pem -CAcreateserial \
    -days 825 -extfile broker.ext -out broker.cert.pem
cat > mosquitto.conf <<EOF
listener 8883 192.168.1.173
cafile $HOME/yocto/fleet-ca/ca.cert.pem
certfile $HOME/yocto/fleet-ca/broker.cert.pem
keyfile $HOME/yocto/fleet-ca/broker.key.pem
require_certificate true
use_identity_as_username true
allow_anonymous true
EOF
mosquitto -c mosquitto.conf
```

With `require_certificate`, only clients whose certificate the fleet CA
issued get past the TLS handshake. `use_identity_as_username` makes each
certificate's name the client's user name, which per-node ACLs can then
use. The host firewall must let the Pi reach port 8883 (docs/lab.md has the
rule for the test port).

### Each node: its identity

The node's certificate, named after the node:

```sh
cd ~/yocto/fleet-ca
openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout raspberrypi5.key.pem -out raspberrypi5.csr -subj "/CN=raspberrypi5"
printf '%s\n' "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" \
    "extendedKeyUsage=clientAuth" > client.ext
openssl x509 -req -in raspberrypi5.csr -CA ca.cert.pem -CAkey ca.key.pem -CAcreateserial \
    -days 825 -extfile client.ext -out raspberrypi5.cert.pem
printf '%s\n' "mqtt_broker = 192.168.1.173" \
    "mqtt_ca = /data/edgenode/mqtt/ca.cert.pem" \
    "mqtt_cert = /data/edgenode/mqtt/node.cert.pem" \
    "mqtt_key = /data/edgenode/mqtt/node.key.pem" > raspberrypi5.conf
```

On the development image, booted from its card, root logs in over SSH
without a password:

```sh
cd ~/yocto/fleet-ca
ssh root@192.168.1.164 mkdir -p /data/edgenode/mqtt
ssh root@192.168.1.164 'cat > /data/edgenode/edgenoded.conf' < raspberrypi5.conf
ssh root@192.168.1.164 'cat > /data/edgenode/mqtt/ca.cert.pem' < ca.cert.pem
ssh root@192.168.1.164 'cat > /data/edgenode/mqtt/node.cert.pem' < raspberrypi5.cert.pem
ssh root@192.168.1.164 'cat > /data/edgenode/mqtt/node.key.pem' < raspberrypi5.key.pem
ssh root@192.168.1.164 systemctl restart edgenoded-site.service edgenoded.service
ssh root@192.168.1.164 journalctl -b -u edgenoded | grep MQTT
```

The last command should show `Connected to MQTT broker 192.168.1.173:8883
over TLS`. Then delete `raspberrypi5.key.pem` from the build host: the
node's copy is the only one it needs.

If it shows `certificate verify failed` instead, the brackets after it say
which check failed:

- `IP address mismatch` or `hostname mismatch`: the broker's certificate
  was issued for another address than `mqtt_broker`.
- `self-signed certificate in certificate chain` or `unable to get local
  issuer certificate`: `mqtt_ca` is not the CA that issued the broker's
  certificate.
- `certificate has expired` or `certificate is not yet valid`: check the
  certificate's dates, and the node's clock.

The production image has no password login. There the files go onto the
card's data partition before the card goes into the node: the sixth
partition, an ext4 file system, under `edgenode/` as in the table above.

## In production

A production line would make the key on the node, or in a secure element,
and send out only a certificate request. Provisioning would then go
through an enrolment service rather than over SSH. The layout and the
permissions above stay the same.
