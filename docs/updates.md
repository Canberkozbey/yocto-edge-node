# Updates

How the Raspberry Pi 5 node is updated, and what to do by hand: signing
keys, building, writing a card, installing a bundle. The design and its
reasons are in [ADR 0008](adr/0008-ab-updates-with-rauc-and-tryboot.md);
the read-only root filesystem verified by dm-verity in
[ADR 0009](adr/0009-read-only-root-with-dm-verity.md).

## Signing keys (once per build host)

Update bundles are signed, and every image trusts one CA certificate. The
keys are never in the repository. Create development keys on the build
host:

```sh
tools/make-signing-keys.sh dev
```

| File | What it is | Who reads it |
| --- | --- | --- |
| `~/yocto/keys/ca.cert.pem` | CA certificate, the keyring in every image | the build (`rauc-conf`) |
| `~/yocto/keys/signing.cert.pem` | Signing certificate, issued by the CA for code signing | the build (bundles) |
| `~/yocto/keys/signing.key.pem` | Signing key | the build (bundles) |
| `~/yocto/keys-ca/ca.key.pem` | CA key, issues signing certificates | nobody during builds |

The script refuses to replace existing keys. Images trust only the CA they
were built with, so new keys mean a newly written SD card. Anyone holding
these keys can sign updates for images built with them; they are for
development only. Releases are signed with separate release keys that only
CI's release job sees ([releases.md](releases.md)), so a development bundle
does not install on a released image, nor the other way round.

## Building

The build container gets the key directory read-only, at `/keys`
(`EDGENODE_KEYS_DIR`):

```sh
kas-container --runtime-args "-v $HOME/yocto/keys:/keys:ro" \
    build kas/base.yml:kas/machine/raspberrypi5.yml
```

Without the keys the build stops with a message pointing here. The QEMU
build needs no keys; it has no update system.

The deploy directory (`tmp/deploy/images/raspberrypi5/`) then has:

| File | Use |
| --- | --- |
| `edgenode-image-raspberrypi5.rootfs.wic.bz2` and `.wic.bmap` | SD card image: the production image in slot A |
| `edgenode-image-dev-raspberrypi5.rootfs.wic.bz2` and `.wic.bmap` | The same with the development image |
| `edgenode-bundle-raspberrypi5.raucb` | Update bundle: the production image |
| `edgenode-bundle-dev-raspberrypi5.raucb` | Update bundle: the development image (tests) |
| `<image>-raspberrypi5.rootfs.verity` | The root filesystem with its dm-verity hash tree: what a slot holds |
| `<image>-raspberrypi5.rootfs.verity.env` | Its verity parameters, root hash included |
| `<image>-raspberrypi5.rootfs.bootfs.vfat` | The boot slot image whose command lines carry that root hash |

## Writing a card

The SD card image holds all six partitions; slot A gets this build, slot B
starts empty. Check the card's device name with `lsblk` first: everything on
it is overwritten.

```sh
sudo bmaptool copy \
  $KAS_WORK_DIR/build/tmp/deploy/images/raspberrypi5/edgenode-image-dev-raspberrypi5.rootfs.wic.bz2 \
  /dev/sdX
```

The card's slots need bootloader EEPROM 2025-03-10 or later, which picks
each slot's kernel command line (docs/lab.md, bootloader settings). With an
older one the board still boots slot A, but without `rauc.slot` on its
command line, so RAUC cannot tell which slot runs.

## Installing an update

On the node (development image; the production image allows root over SSH
by key only):

```sh
ssh root@192.168.1.164 'cat > /tmp/update.raucb' < edgenode-bundle-dev-raspberrypi5.raucb
ssh root@192.168.1.164
rauc install /tmp/update.raucb
edgenode-boot status        # next: B (trial)
systemctl reboot            # not a power cut: that cancels the trial
```

The other slot boots once on trial. About 15 seconds after edgenoded is up,
`edgenode-boot-commit` commits it:

```sh
edgenode-boot status        # default: B, booted: B
rauc status                 # booted from rootfs.1 (B), both slots good
journalctl -u edgenode-boot-commit
```

If edgenoded does not come up within 60 seconds of a trial boot, the slot
is marked bad and the board reboots into the previous one. The same happens
when the trial boot never gets that far: a kernel that cannot mount a root
filesystem failing verification panics and reboots, and after a power cut
the firmware starts the previous slot anyway. Back there,
`edgenode-boot-commit` marks the slot whose trial did not report back.
`rauc status` then shows the new slot as bad.

| Command | What it does |
| --- | --- |
| `edgenode-boot status` | Default slot, booted slot, the slot of the next boot, bad marks |
| `rauc status --detailed` | RAUC's view, with each slot's installed bundle |
| `rauc status mark-active other` | Boot the other slot next, on trial |
| `rauc status mark-good` | Commit the booted slot by hand |

Marking the booted, committed slot bad is refused: it is the only slot
known to work.

## Read-only root

`/` is the dm-verity device `vroot` over the booted slot's partition,
mounted read-only: nothing on it changes, not even as root. Logs and other
state are on tmpfs and gone after a reboot; what must survive goes to
`/data`. SSH host keys are made at each boot, so clients see a new host key
after every reboot (persistent keys on `/data` come with stage 4).

## The tests

`tests/update` runs all of the above on the lab's Pi, from writing the card
to rollback, power cuts and the watchdog (README, Test). It rewrites the SD
card, and leaves the Pi booting from it.
