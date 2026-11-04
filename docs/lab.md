# The test lab

The hardware the tests run on, how it is connected, and the one-time setup
of the build host. Lab-specific values (addresses, paths, the serial port)
are in `lab/lab.toml` and `tests/env/pi5-*.yaml`.

## Hardware

| Part | Role |
| --- | --- |
| Raspberry Pi 5 | The target. Boots over the network from the build host, or from its SD card when nothing is served. |
| USB serial cable (PL2303, 3.3 V logic) | Console on the 40-pin header: GND to pin 6, the cable's RX to pin 8 (GPIO14, Pi TX), its TX to pin 10 (GPIO15, Pi RX). The cable's 5 V wire stays unconnected. |
| Official 27 W power supply | Powers the Pi. A switchable smart plug (Shelly Gen3) will let labgrid power-cycle it; until then labgrid asks on the terminal. |
| USB stick with Raspberry Pi OS | Maintenance only: bootloader (EEPROM) updates and settings. The edge node images carry no EEPROM tools. |

## Network

| Host | Address | Role |
| --- | --- | --- |
| Router | 192.168.1.1, DHCP server | Reserves 192.168.1.164 for the Pi's MAC address, 98:fe:54:0e:f2:b6, so no other device gets it |
| Build host | 192.168.1.173 (enp3s0) | Builds, runs the tests, serves network boot: proxy DHCP and TFTP (dnsmasq), NFS |
| Raspberry Pi 5 | 192.168.1.164 | Target |

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

## Known gaps

- Power is switched by hand until the smart plug is in place.
