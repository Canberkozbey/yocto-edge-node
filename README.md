# Edge Node — Yocto build, CI and hardware-in-the-loop testing

A small but complete embedded Linux product setup: a secure, remotely
updatable industrial edge node built with the Yocto Project, with builds and
tests that run automatically, in QEMU and on real hardware.

> **Status:** Stages 1 (layer, pinned build, QEMU, CI), 2 (Raspberry Pi 5
> hardware-in-the-loop), 3 (A/B updates with rollback, read-only root
> verified by dm-verity) and 4 (dev/prod split, license policy, SBOM and CVE
> reports, signed releases with build provenance, MQTT over mutual TLS with
> a device identity that updates keep) done.

## What is here

| Path | Purpose |
| --- | --- |
| `meta-edgenode/` | The layer: distro policy, image and the node's own recipes |
| `meta-edgenode/dynamic-layers/` | Metadata parsed only when another layer is present: the Raspberry Pi BSP, or meta-rauc for the update system |
| `meta-edgenode/recipes-edgenode/` | `edgenoded`, the node's service (health and sensor readings over HTTP, readings on the display and over MQTT), and its configuration |
| `meta-edgenode/recipes-kernel/edgenode-ads1115/` | The node's own kernel driver: ADS1115 ADC through IIO ([ADR 0004](docs/adr/0004-own-ads1115-driver.md)) |
| `kas/base.yml` | Pinned upstream layers, distro, image and build policy |
| `kas/base.lock.yml` | Pinned revisions of third-party layers (`kas lock`) |
| `kas/machine/` | One file per target machine |
| `kas/cve-check.yml` | Adds the CVE report to a build (CI's hardware job, releases) |
| `kas/release.yml` | The release build: production image and bundle, signed with the release keys |
| `tests/` | pytest integration tests against a running image; `requirements.txt` pins their dependencies with hashes |
| `tools/netboot.py` | Serves a built image to the lab's Raspberry Pi 5 over the network |
| `tools/make-signing-keys.sh` | Creates the keys that sign update bundles, development or release, outside the repository |
| `tools/cve-summary.py`, `tools/release-notes.sh` | The CVE summary and release notes that CI writes |
| `lab/` | This lab's configuration: addresses, dnsmasq and NFS export files, the sudo rule for CI and the CI runner's job hook |
| `docs/lab.md` | The test lab: hardware, wiring, network boot, host setup |
| `docs/updates.md` | Updates: signing keys, SD card, installing a bundle |
| `docs/releases.md` | Releases: what one holds, how to verify it, how CI makes it |
| `docs/provisioning.md` | Provisioning a node: the site's configuration and its MQTT identity on the data partition |
| `docs/adr/` | Architecture decision records |
| `SECURITY.md` | Reporting a vulnerability, and what the node and the lab protect against and what they do not |
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
| `raspberrypi5` | `kas/machine/raspberrypi5.yml` | Hardware target: boots from an SD card with A/B slots, a read-only root verified by dm-verity, updates through RAUC; serial console on the 40-pin header (GPIO14/15) |

The Raspberry Pi 5 build also makes signed update bundles. Their keys stay
outside the repository: create development keys once with
`tools/make-signing-keys.sh dev` and give the build container their directory
([docs/updates.md](docs/updates.md)). Write the SD card image with
`bmaptool` (Ubuntu package `bmap-tools`). Check the card's device name with
`lsblk` first: everything on it is overwritten.

```sh
tools/make-signing-keys.sh dev   # once: ~/yocto/keys, and the CA key in ~/yocto/keys-ca
kas-container --runtime-args "-v $HOME/yocto/keys:/keys:ro" \
  build kas/base.yml:kas/machine/raspberrypi5.yml

sudo bmaptool copy \
  $KAS_WORK_DIR/build/tmp/deploy/images/raspberrypi5/edgenode-image-dev-raspberrypi5.rootfs.wic.bz2 \
  /dev/sdX
```

Every build writes an SBOM per image next to it (`<image>.spdx.json`,
SPDX 3.0). Adding `kas/cve-check.yml` to the kas files also writes a CVE
report from it, as CI's hardware job does; that build needs network access
([ADR 0010](docs/adr/0010-sbom-cve-reports-and-signed-releases.md)).

## Images

| Image | For | Access | License policy |
| --- | --- | --- | --- |
| `edgenode-image` | Production | Root account locked, no password login; SSH takes root by key only, and the image holds no key ([SECURITY.md](SECURITY.md)) | No (L/A)GPLv3; the build fails otherwise |
| `edgenode-image-dev` | Development | Root without a password on the console and over SSH; `gdbserver`, `strace` and `i2c-tools`; in QEMU, the vkms module for the display tests | None (`gdbserver` is GPLv3) |

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
| `tests/image` | both images, not booted | package manifests and files read from the ext4 images with `debugfs`, SBOMs and CVE reports; on the Raspberry Pi 5 also the SD card layout, boot partitions, dm-verity hash trees, and the update bundles and their signatures |
| `tests/update` | development image on the Raspberry Pi 5's SD card | writes the card, then installs updates, rolls back and cuts the power |

```sh
sudo apt install qemu-system-arm python3-venv mosquitto
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

# A/B updates: rewrites the SD card, starting from network boot
sudo tools/netboot.py deploy edgenode-image-dev --images "$LG_IMAGES"
.venv/bin/pytest --lg-env tests/env/pi5-dev.yaml tests/update
```

What the tests check:

- boot finishes with no failed units, on the `edgenode` distro, with systemd as init
- `edgenoded` is active, runs as an unprivileged user and has its watchdog armed
- `/health` answers on the target and from the host, and reports the installed version;
  a client that sends its request a byte at a time is cut off after 2 s and
  cannot hold up the service's answers
- `/sensors` reads each configured analog input through IIO and scales it
  to millivolts, and reports an unreadable input without hiding the others
  (on every machine, against a stand-in IIO device made of plain files);
  the Raspberry Pi 5's configuration names its ADC and inputs, and there
  the service's own `/sensors` reads the fixed references from the host
- the readings appear on a DRM/KMS display: in QEMU, an extra instance draws
  on vkms (a virtual display), whose frame CRC holds still while nothing
  changes and changes after a reading does; on the Raspberry Pi 5, the
  service's framebuffer is on the panel in its 240x240 mode, and the
  console gets the panel back when the service stops
- the readings go out over MQTT to a broker the tests start on the host:
  the `/sensors` JSON and an `online` status, both retained for late
  subscribers; changes follow, the connection comes back after a broker
  restart, and the status turns `offline` on a clean stop and, through the
  last will, after `kill -9`; no image names a broker
- over mutual TLS, with certificates made for the run: the node publishes
  to a broker that takes only certificates from its CA; it refuses a broker
  certificate from another CA or for another address; the broker refuses a
  node without a certificate or with one from another CA; the site's file
  on `/data` sets the broker and the identity, and a broken site file or an
  unreadable key leaves the node running without MQTT
- production: root's console login asks for a password, SSH rejects root
  without one, root's password field is locked, sshd allows neither empty
  passwords nor root password logins, and no debug tools are installed
- no graphics stack slipped into either image, and nothing under (L/A)GPLv3 in the production image (from the license manifests)
- each image's SBOM (SPDX 3.0) has every installed package; where the build
  ran the CVE check, each image has a CVE report that covers the kernel and
  the node's own service (report only: known CVEs do not fail the build)
- no Wi-Fi or Bluetooth firmware in either image; on the Raspberry Pi 5,
  the console is on the header UART, both radios and VC4 graphics are off
  in `config.txt`, and the running system has no wireless network or
  Bluetooth devices
- on the Raspberry Pi 5, the ADS1115 ADC reads its fixed references (GND and
  3.3 V) within tolerance, with the full scale set in the device tree; the
  ST7789 display registers as a 240x240 DRM display, with its init sequence
  compiled at build time, is the only graphics device (no VC4 or sound
  driver registers), and its backlight follows its control;
  hardware tests carry labgrid's `lg_feature` marker and are skipped where
  the environment lacks that hardware
- on the Raspberry Pi 5, the SD card image has the A/B layout, each boot
  slot names its own root filesystem with this image's dm-verity root hash,
  each root filesystem's hash tree checks out against that hash, and each
  update bundle carries this build's slots byte for byte, signed by a
  certificate for code signing that chains to the CA in the image it
  carries, and the images trust the CA that CI names; on the board, the
  root filesystem is the verity device and cannot be written, an update
  boots on trial and is committed, a power cut before the trial boot or
  during installation keeps the running slot, a release whose service fails
  and a root filesystem changed by one block both roll back by themselves,
  bundles with a broken signature or payload are rejected, and the hardware
  watchdog resets a hung kernel; the SSH host keys and an MQTT identity
  provisioned on `/data` (the key readable by root only) bring the node
  online over mutual TLS, end up readable by the daemon's group only, and
  are still the node's after an update

Each production check has a control in the development image that must give
the opposite result, so a check that cannot tell the two apart fails.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) has two jobs, for
pushes to `main` and manual runs. The first builds both images for QEMU and
runs the tests there. The second builds for the Raspberry Pi 5, serves each
image to the lab's board over the network and runs the same tests on it,
then writes its SD card and runs the update tests; how CI gets the
privileges that takes is in [ADR 0005](docs/adr/0005-ci-on-lab-hardware.md).
Both run on a self-hosted runner with the label `yocto`: a Yocto build needs
more memory and disk than hosted runners offer, and the runner keeps the
download and shared state caches between runs. Console logs and JUnit
reports are kept as build artifacts. The hardware job also runs the CVE
check (`kas/cve-check.yml`): it puts a CVE summary on the run's page and
keeps the SBOMs and reports as artifacts.

The self-hosted runner is the lab's build host, so it never takes a pull
request: a pull request from a fork could run any code there. Pull requests
get [`.github/workflows/checks.yml`](.github/workflows/checks.yml) instead,
on a GitHub hosted runner: actionlint and zizmor for the workflows,
shellcheck for the shell scripts and pyflakes for the Python code. The
runner enforces this itself, whatever a workflow file says, with a hook
that refuses every job but pushes to `main`, release tags and manual runs
([`lab/runner/job-started.sh`](lab/runner/job-started.sh)). The test
dependencies are pinned with the hashes of their files
(`tests/requirements.txt`), as the runner installs them on every run.

[`.github/workflows/release.yml`](.github/workflows/release.yml) makes a
release from a `v*` tag on a commit that CI has passed. A job on the
self-hosted runner builds the production image with the release keys,
which only it sees, and runs the image tests; a second job, on a GitHub
hosted runner, attests the files' build provenance and publishes them: the
SD card image, the update bundle, the SBOM, the CVE report and checksums
([docs/releases.md](docs/releases.md)). Only that second job can write
releases and attestations, so no code that runs during the build can.

Runner host requirements: Docker, kas (with `kas-container` on `PATH`),
`qemu-system-arm`, `python3-venv`, `mosquitto` (the tests start their
own broker; the system service can stay off), `mtools`, `squashfs-tools`
and `cryptsetup-bin`, and the update signing keys in `~/yocto/keys`
([docs/updates.md](docs/updates.md)). Its setup, the hook included, is in
[docs/lab.md](docs/lab.md).

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
- **The display through KMS, without libdrm.** A dumb buffer and the
  kernel's mode-setting ioctls; a BSD-2-Clause bitmap font converted at
  build time. Details: [ADR 0006](docs/adr/0006-display-through-kms.md).
- **MQTT with libmosquitto, no broker in the image.** Retained readings
  and an online/offline status with a last will.
  Details: [ADR 0007](docs/adr/0007-mqtt-with-libmosquitto.md).
- **A/B updates with RAUC, slots chosen by the Pi's own firmware.** No
  U-Boot: `autoboot.txt` and the firmware's one-shot tryboot flag, driven
  by our own RAUC backend; a health check commits a new slot or rolls it
  back; signed verity bundles, keys outside the repository.
  Details: [ADR 0008](docs/adr/0008-ab-updates-with-rauc-and-tryboot.md).
- **Read-only root filesystem verified by dm-verity, without an
  initramfs.** Each image hashes itself at build time and writes the root
  hash into its boot slot's command lines; the kernel sets the verity device
  up from `dm-mod.create=`. Details:
  [ADR 0009](docs/adr/0009-read-only-root-with-dm-verity.md).
- **SBOM on every build, CVE report from the SBOM, report only.** oe-core's
  create-spdx and sbom-cve-check; findings go to a person, not a build
  gate. **Releases signed with their own keys,** kept in a GitHub
  environment that only `v*` tags reach, and every release file attested
  through Sigstore. Details:
  [ADR 0010](docs/adr/0010-sbom-cve-reports-and-signed-releases.md).
- **The node's identity on the data partition, MQTT over mutual TLS.** The
  broker, the node's key and its certificates are the site's, on `/data`,
  in a file that `/etc/edgenoded.conf` names; only the daemon's group reads
  them, and a broken identity leaves the node running without MQTT. The
  SSH host keys stay there too. Details:
  [ADR 0011](docs/adr/0011-device-identity-and-mqtt-over-mutual-tls.md),
  [docs/provisioning.md](docs/provisioning.md).

## Roadmap

1. Layer, pinned build, QEMU boot, pytest, CI
2. Raspberry Pi 5 as hardware-in-the-loop target, own kernel driver (ADS1115 ADC over I2C), SPI display, MQTT publishing
3. A/B updates with RAUC, read-only rootfs with dm-verity, update and rollback tests
4. Dev/prod split, license policy, SBOM and CVE reports, signed releases, MQTT over mutual TLS with a device identity

## License

MIT, see [LICENSE](LICENSE). The kernel driver and the device tree overlays
are GPL-2.0-only, as code built into or loaded by the Linux kernel must be;
their files say so in their SPDX headers. `edgenoded` compiles in the Spleen
font (BSD-2-Clause), fetched at build time; its recipe declares both licenses.
