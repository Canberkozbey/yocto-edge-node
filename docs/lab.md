# The test lab

The hardware the tests run on, how it is connected, and the one-time setup
of the build host. Lab-specific values (addresses, paths, the serial port)
are in `lab/lab.toml` and `tests/env/pi5-*.yaml`.

## Hardware

| Part | Role |
| --- | --- |
| Raspberry Pi 5 | The target. Boots over the network from the build host, or from its SD card when nothing is served. |
| USB serial cable (PL2303, 3.3 V logic) | Console on the 40-pin header: GND to pin 6, the cable's RX to pin 8 (GPIO14, Pi TX), its TX to pin 10 (GPIO15, Pi RX). The cable's 5 V wire stays unconnected. |
| Official 27 W power supply | Powers the Pi, through the smart plug. |
| Shelly Plug M Gen3 (S3PL-30110EU) | Switches the Pi's power over its local HTTP API, so labgrid can power-cycle it. Device authentication off (labgrid's Shelly backend sends plain requests); after a power cut it restores its last state. |
| USB stick with Raspberry Pi OS | Maintenance only: bootloader (EEPROM) updates and settings. The edge node images carry no EEPROM tools. |
| ADS1115 ADC module and a 10 kΩ potentiometer | Analog input on the header's I2C bus; wiring below. |
| 1.3 inch 240x240 display, ST7789 controller (7 pins, no chip select) | Local display on SPI0; wiring below. |

## ADC wiring

The ADS1115 runs from the Pi's 3.3 V, so every analog input must stay
between GND and 3.3 V. Nothing on it connects to the header's 5 V pins
(2 and 4). The breadboard's power rails take 3.3 V from pin 1 and GND from
pin 9.

| ADS1115 pin | Connects to |
| --- | --- |
| VDD | 3.3 V rail (Pi pin 1) |
| GND | GND rail (Pi pin 9) |
| SCL | Pi pin 5 (GPIO3, I2C SCL) |
| SDA | Pi pin 3 (GPIO2, I2C SDA) |
| ADDR | GND rail: I2C address 0x48 |
| ALERT | not connected |
| A0 | potentiometer wiper; its outer legs to the 3.3 V and GND rails |
| A1 | GND rail: 0 V reference |
| A2 | 3.3 V rail: supply reference |
| A3 | not connected |

A1 and A2 give the tests readings known in advance. The potentiometer is
for trying the ADC by hand; the tests only check that it reads between the
two references.

The images drive the ADC with the project's own driver (`edgenode-ads1115`),
validated against the mainline driver on this hardware
([ADR 0004](adr/0004-own-ads1115-driver.md)).

## Display wiring

The module's SCL and SDA pins are the SPI clock and data lines, not I2C.
It has no chip select pin (the controller's is tied low on the module), so
the kernel drives it in SPI mode 3.

| Display pin | Connects to |
| --- | --- |
| GND | GND rail |
| VCC | 3.3 V rail |
| SCL | Pi pin 23 (GPIO11, SPI0 SCLK) |
| SDA | Pi pin 19 (GPIO10, SPI0 MOSI) |
| RES | Pi pin 16 (GPIO23, reset) |
| DC | Pi pin 18 (GPIO24, data/command) |
| BLK | Pi pin 12 (GPIO18, backlight) |

The tests check what the kernel reports; the picture itself is a manual
check. The display appears as a framebuffer too: find it by name and fill it
with noise.

```sh
grep . /sys/class/graphics/fb*/name
cat /dev/urandom > /dev/fbN      # N: the display's framebuffer
```

## Network

| Host | Address | Role |
| --- | --- | --- |
| Router | 192.168.1.1, DHCP server | Reserves 192.168.1.164 for the Pi's MAC address, 98:fe:54:0e:f2:b6, so no other device gets it |
| Build host | 192.168.1.173 (enp3s0) | Builds, runs the tests, serves network boot: proxy DHCP and TFTP (dnsmasq), NFS |
| Raspberry Pi 5 | 192.168.1.164 | Target |
| Shelly Plug M Gen3 | 192.168.1.114 (reserved on the router) | The Pi's power switch |

## Network boot

1. The Pi's bootloader tries the network first. It asks for an address by
   DHCP; the router answers with one, and dnsmasq on the build host adds the
   boot server to the answer (proxy DHCP, for the Pi's MAC address only).
2. The bootloader fetches its boot files over TFTP from a directory named
   after its MAC address: `config.txt`, the kernel, the device tree,
   overlays and `cmdline.txt`.
3. The kernel configures `eth0` with the Pi's reserved address, set
   statically on the kernel command line, and mounts its root filesystem
   over NFS. Static, because the router did not always give the kernel's
   own DHCP client the reserved address, and both the NFS export and the
   tests expect that one.
4. When the directory for the Pi is missing, network boot fails and the
   bootloader boots the SD card instead.

`tools/netboot.py` fills the TFTP directory and the NFS root from a build.
The boot files are copied out of the image's own boot partition, so the
network boot uses the same files as the SD card, apart from the kernel
command line.

```sh
sudo tools/netboot.py deploy edgenode-image-dev --images "$LG_IMAGES"   # or edgenode-image
sudo tools/netboot.py remove                                             # back to the SD card
```

Deploy while the Pi is off or about to be power-cycled: replacing the root
filesystem under a running system breaks it.

## Build host setup (once)

```sh
sudo apt install dnsmasq nfs-kernel-server mtools zstd
sudo mkdir -p /srv/edgenode/tftp /srv/edgenode/nfs /etc/exports.d
sudo cp lab/dnsmasq.d/edgenode.conf /etc/dnsmasq.d/
sudo cp lab/exports.d/edgenode.exports /etc/exports.d/
sudo exportfs -ra
echo 'DNSMASQ_EXCEPT="lo"' | sudo tee -a /etc/default/dnsmasq
sudo systemctl restart dnsmasq
```

dnsmasq may fail to start right after installation: by default it also
serves DNS on port 53, which systemd-resolved already uses. The lab
configuration turns DNS off (`port=0`), so the restart above succeeds.
`DNSMASQ_EXCEPT="lo"` stops Ubuntu's service wrapper from registering
dnsmasq as the host's DNS resolver, which would point the host at a DNS
server that is turned off.
`journalctl -u dnsmasq -f` shows each DHCP exchange and TFTP transfer.

## CI access (once)

The CI runner serves images through a root-owned copy of `tools/netboot.py`
that a sudo rule allows without a password ([ADR 0005](adr/0005-ci-on-lab-hardware.md)).
Set `RUNNER_USER` to the account the runner service runs as, which
`ps -o user= -C Runner.Listener` shows.

```sh
sudo groupadd --system edgenode-lab
sudo usermod -aG edgenode-lab,dialout "$RUNNER_USER"
sudo install -m 0755 tools/netboot.py /usr/local/sbin/edgenode-netboot
sudo install -D -m 0644 lab/lab.toml /etc/edgenode/lab.toml
sudo install -m 0440 lab/sudoers.d/edgenode-netboot /etc/sudoers.d/edgenode-netboot
sudo visudo -cf /etc/sudoers.d/edgenode-netboot
```

Then restart the runner service so it gets the new groups (in the runner's
directory: `sudo ./svc.sh stop && sudo ./svc.sh start`). After a change to
`tools/netboot.py` or `lab/lab.toml`, repeat the matching `install` line;
the CI job warns while the installed tool differs.

## Bootloader settings (once)

Take out the SD card, boot Raspberry Pi OS from the USB stick, log in over
SSH and edit the bootloader configuration:

```sh
sudo rpi-eeprom-config --edit
```

```ini
BOOT_ORDER=0xf412
TFTP_PREFIX=2
```

`BOOT_ORDER` is read from right to left: network (2), SD card (1), USB (4),
then start over (f). `TFTP_PREFIX=2` makes the bootloader request its files
from a directory named after its MAC address (`98-fe-54-0e-f2-b6/`). The
settings take effect after a reboot. When the build host is off, the
bootloader waits for a boot server before it moves on to the SD card
(`DHCP_TIMEOUT`, about 45 seconds by default).
