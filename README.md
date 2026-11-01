# Edge Node — Yocto build, CI and hardware-in-the-loop testing

A small but complete embedded Linux product setup: a secure, remotely
updatable industrial edge node built with the Yocto Project, with builds and
tests that run automatically, in QEMU and on real hardware.

> **Status:** Stage 1 (layer, pinned build, QEMU) in progress.

## What is here

| Path | Purpose |
| --- | --- |
| `meta-edgenode/` | The layer: distro policy, image and the node's own recipes |
| `meta-edgenode/dynamic-layers/` | Board-specific metadata, parsed only when that BSP layer is present |
| `kas/base.yml` | Pinned upstream layers, distro, image and build policy |
| `kas/machine/` | One file per target machine |
| `tests/` | pytest integration tests against a running image |
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

## Test

Integration tests use [pytest](https://pytest.org) with
[labgrid](https://labgrid.readthedocs.io). labgrid boots the built image in
QEMU, logs in over the serial console and forwards a host port to the
daemon's HTTP endpoint. The tests only talk to labgrid drivers, so the same
tests can run on real hardware with a different environment file.

```sh
sudo apt install qemu-system-arm python3-venv
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt

export LG_IMAGES=$KAS_WORK_DIR/build/tmp/deploy/images/qemuarm64
.venv/bin/pytest --lg-env tests/env/qemu.yaml tests/
```

What the tests check:

- boot finishes with no failed units, on the `edgenode` distro, with systemd as init
- no graphics stack slipped into the image
- `edgenoded` is active, runs as an unprivileged user and has its watchdog armed
- `/health` answers on the target and from the host, and reports the installed version

## Design notes

- **Upstream repositories, not the combined poky repository.** Since Yocto
  5.3 the poky repository is no longer updated; bitbake, openembedded-core
  and meta-yocto are used as separate repositories. kas pins each one.
- **Pinned to release tags and commits.** Upstream layers follow the
  `yocto-6.0.x` point releases. Third-party layers use `kas lock`.
  Details: [ADR 0001](docs/adr/0001-build-setup-with-kas.md).
- **Own distro on top of Poky.** `edgenode.conf` keeps distro policy
  separate from the image and from machine configuration.

## Roadmap

1. Layer, pinned build, QEMU boot, pytest, CI
2. Raspberry Pi 5 as hardware-in-the-loop target, own kernel driver (ADS1115 ADC over I2C), SPI display
3. A/B updates with RAUC, read-only rootfs with dm-verity, update and rollback tests
4. Dev/prod split, license policy, SBOM and CVE reports, signed releases

## License

MIT, see [LICENSE](LICENSE).
