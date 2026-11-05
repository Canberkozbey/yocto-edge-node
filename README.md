# Edge Node — Yocto build, CI and hardware-in-the-loop testing

A small but complete embedded Linux product setup: a secure, remotely
updatable industrial edge node built with the Yocto Project, with builds and
tests that run automatically, in QEMU and on real hardware.

> **Status:** Stage 1 (layer, pinned build, QEMU, CI) done, as are the
> dev/prod split and license policy from stage 4. Stage 2 (Raspberry Pi 5)
> in progress.

## What is here

| Path | Purpose |
| --- | --- |
| `meta-edgenode/` | The layer: distro policy, image and the node's own recipes |
| `meta-edgenode/dynamic-layers/` | Board-specific metadata, parsed only when that BSP layer is present |
| `meta-edgenode/recipes-kernel/edgenode-ads1115/` | The node's own kernel driver: ADS1115 ADC through IIO ([ADR 0004](docs/adr/0004-own-ads1115-driver.md)) |
| `kas/base.yml` | Pinned upstream layers, distro, image and build policy |
| `kas/base.lock.yml` | Pinned revisions of third-party layers (`kas lock`) |
| `kas/machine/` | One file per target machine |
| `tests/` | pytest integration tests against a running image |
| `tools/netboot.py` | Serves a built image to the lab's Raspberry Pi 5 over the network |
| `lab/` | This lab's configuration: addresses, dnsmasq and NFS export files |
| `docs/lab.md` | The test lab: hardware, wiring, network boot, host setup |
| `docs/adr/` | Architecture decision records |
| `docs/migration-kirkstone-to-wrynose.md` | Notes from moving from Yocto 4.0 habits to 6.0 |

## Build

Requirements: a Linux host with Docker and [kas](https://kas.readthedocs.io)
(5.x). Yocto 6.0 asks for 32 GB RAM and 140 GB of free disk; this setup keeps
disk use well below that with `rm_work`.

```sh
# Keep sources, downloads and shared state outside the repository
export KAS_WORK_DIR=~/yocto/work
export DL_DIR=~/yocto/cache/downloads
export SSTATE_DIR=~/yocto/cache/sstate
mkdir -p "$KAS_WORK_DIR" "$DL_DIR" "$SSTATE_DIR"

kas-container build kas/base.yml:kas/machine/qemuarm64.yml
```

The first build compiles the toolchain and takes a few hours. Later builds
reuse shared state and take minutes.

| Machine | kas file | Used for |
| --- | --- | --- |
| `qemuarm64` | `kas/machine/qemuarm64.yml` | CI on every push; boots without hardware |
| `raspberrypi5` | `kas/machine/raspberrypi5.yml` | Hardware target: boots from an SD card, serial console on the 40-pin header (GPIO14/15) |

For the Raspberry Pi 5, build with its machine file and write the SD card
image with `bmaptool` (Ubuntu package `bmap-tools`). Check the card's device
name with `lsblk` first: everything on it is overwritten.

```sh
kas-container build kas/base.yml:kas/machine/raspberrypi5.yml

sudo bmaptool copy \
  $KAS_WORK_DIR/build/tmp/deploy/images/raspberrypi5/edgenode-image-dev-raspberrypi5.rootfs.wic.bz2 \
  /dev/sdX
```

## Images

| Image | For | Access | License policy |
| --- | --- | --- | --- |
| `edgenode-image` | Production | Root account locked, no password login; SSH root login by key only (OpenSSH default) | No (L/A)GPLv3; the build fails otherwise |
| `edgenode-image-dev` | Development | Root without a password on the console and over SSH; `gdbserver` and `strace` | None (`gdbserver` is GPLv3) |

The development image is the production image plus those extras
(`require edgenode-image.bb`), so both share every other package. Why the
production image excludes GPLv3:
[ADR 0002](docs/adr/0002-no-gplv3-in-production.md).

## Test

Integration tests use [pytest](https://pytest.org) with
[labgrid](https://labgrid.readthedocs.io). labgrid boots the built image in
QEMU, talks to it over the serial console and forwards host ports to the
target's services. The tests only use labgrid drivers, so the same tests run
on the Raspberry Pi 5 with a different environment file.

| Directory | Runs against | How |
| --- | --- | --- |
| `tests/dev` | development image | logs in as root on the console |
| `tests/prod` | production image | no login possible: console prompts and forwarded ports only |
| `tests/image` | both images, not booted | package manifests and files read from the ext4 images with `debugfs` |

```sh
sudo apt install qemu-system-arm python3-venv
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt

export LG_IMAGES=$KAS_WORK_DIR/build/tmp/deploy/images/qemuarm64
.venv/bin/pytest --lg-env tests/env/qemu-dev.yaml tests/dev
.venv/bin/pytest --lg-env tests/env/qemu-prod.yaml tests/image tests/prod

# Image checks for another machine; nothing is booted
LG_MACHINE=raspberrypi5 LG_IMAGES=$KAS_WORK_DIR/build/tmp/deploy/images/raspberrypi5 \
  .venv/bin/pytest tests/image
```

On the Raspberry Pi 5, labgrid uses the serial console on the 40-pin header
and reaches the services at the Pi's address, both set in
`tests/env/pi5-*.yaml`. The Pi boots the image the build host serves over
the network, so no SD card has to be rewritten between runs, and labgrid
power-cycles it through a smart plug ([docs/lab.md](docs/lab.md)). Nothing
else (such as a terminal program) may hold the serial port during a run.

```sh
export LG_IMAGES=$KAS_WORK_DIR/build/tmp/deploy/images/raspberrypi5
sudo tools/netboot.py deploy edgenode-image-dev --images "$LG_IMAGES"
.venv/bin/pytest --lg-env tests/env/pi5-dev.yaml tests/dev
sudo tools/netboot.py deploy edgenode-image --images "$LG_IMAGES"
.venv/bin/pytest --lg-env tests/env/pi5-prod.yaml tests/prod
```

What the tests check:

- boot finishes with no failed units, on the `edgenode` distro, with systemd as init
- `edgenoded` is active, runs as an unprivileged user and has its watchdog armed
- `/health` answers on the target and from the host, and reports the installed version
- production: root's console login asks for a password, SSH rejects root
  without one, root's password field is locked, sshd allows neither empty
  passwords nor root password logins, and no debug tools are installed
- no graphics stack slipped into either image, and nothing under (L/A)GPLv3 in the production image (from the license manifests)
- no Wi-Fi or Bluetooth firmware in either image; on the Raspberry Pi 5,
  the console is on the header UART and both radios are off in `config.txt`,
  and the running system has no wireless network or Bluetooth devices
- on the Raspberry Pi 5, the ADS1115 ADC reads its fixed references (GND and
  3.3 V) within tolerance, with the full scale set in the device tree; the
  ST7789 display registers as a 240x240 DRM display, with its init sequence
  compiled at build time, and its backlight follows its control;
  hardware tests carry labgrid's `lg_feature` marker and are skipped where
  the environment lacks that hardware

Each production check has a control in the development image that must give
the opposite result, so a check that cannot tell the two apart fails.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) builds both images and
runs the tests on every push to `main` and on pull requests. It runs on
a self-hosted runner with the label `yocto`: a Yocto build needs more memory
and disk than hosted runners offer, and the runner keeps the download and
shared state caches between runs. Console logs and a JUnit report are kept
as build artifacts.

Runner host requirements: Docker, kas (with `kas-container` on `PATH`),
`qemu-system-arm` and `python3-venv`.

## Design notes

- **Upstream repositories, not the combined poky repository.** Since Yocto
  5.3 the poky repository is no longer updated; bitbake, openembedded-core
  and meta-yocto are used as separate repositories. kas pins each one.
- **Pinned to release tags and commits.** Upstream layers follow the
  `yocto-6.0.x` point releases. Third-party layers use `kas lock`.
  Details: [ADR 0001](docs/adr/0001-build-setup-with-kas.md).
- **Own distro on top of Poky.** `edgenode.conf` keeps distro policy
  separate from the image and from machine configuration.
- **Only what a function needs.** Nothing goes on the device without a
  function that needs it; the node is wired, so Wi-Fi and Bluetooth stay
  out and their radios off.
  Details: [ADR 0003](docs/adr/0003-only-what-a-function-needs.md).

## Roadmap

1. Layer, pinned build, QEMU boot, pytest, CI
2. Raspberry Pi 5 as hardware-in-the-loop target, own kernel driver (ADS1115 ADC over I2C), SPI display
3. A/B updates with RAUC, read-only rootfs with dm-verity, update and rollback tests
4. Dev/prod split, license policy, SBOM and CVE reports, signed releases

## License

MIT, see [LICENSE](LICENSE). The kernel driver and the device tree overlays
are GPL-2.0-only, as code built into or loaded by the Linux kernel must be;
their files say so in their SPDX headers.
