# ADR 0002: No (L/A)GPLv3 software in the production image

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

Later stages lock the device down: signed updates, a read-only root
filesystem verified with dm-verity, and possibly secure boot. GPLv3 and
LGPLv3 require that, for products in their "User Product" scope, the
recipient also gets the information needed to install modified versions of
the covered software. That is hard to reconcile with a locked-down device,
so many embedded products keep GPLv3 software out of production images
altogether. This repository follows that common engineering policy; it is
not a legal assessment.

## Decision

- `edgenode-image` sets
  `INCOMPATIBLE_LICENSE = "GPL-3.0* LGPL-3.0* AGPL-3.0*"`. The image build
  fails if a package under one of those licenses would be installed.
- The wildcards expand to the `-only` and `-or-later` variants only.
  Licenses with exceptions, such as `GPL-3.0-with-GCC-exception` (libgcc),
  are not affected.
- For dual-licensed packages ("LGPL-3.0-or-later | GPL-2.0-or-later") the
  build picks the acceptable branch and records it in the license manifest.
- The policy is set on the production image only. `edgenode-image-dev`
  clears it, because `gdbserver` is GPLv3.
- `tests/image/test_licenses.py` checks the production image's license
  manifest independently, with the development image as a control.

## Alternatives considered

- **Distro-wide `INCOMPATIBLE_LICENSE`:** also stops GPLv3 recipes from
  being built at all, which would remove `gdbserver` from development.
- **Per-package exceptions (`INCOMPATIBLE_LICENSE_EXCEPTIONS`):** kept for
  the case where a GPLv3 package is truly needed; each exception would be
  recorded in a new ADR.

## Consequences

- A dependency change that pulls in, for example, `bash` or `coreutils`
  fails the build and has to be resolved: replace it, make it optional, or
  add a documented exception.
- Enabling the policy surfaced these packages, all resolved without
  exceptions:
  - `tar` (GPL-3.0-only): oe-core's `qemu.inc` adds GNU tar to every image
    on QEMU machines for testimage debug archives. This project tests with
    labgrid, so `kas/machine/qemuarm64.yml` removes it, as oe-core's
    `no-gplv3.inc` does. Busybox still provides a `tar` applet.
  - `kbd-keymaps-pine` (GPL-3.0-or-later): pulled in by systemd's virtual
    console setup through `kbd-keymaps`. The device has no keyboard or local
    display, so the distro drops systemd's `vconsole` option and `kbd` with
    it.
  - `grub-efi` (GPL-3.0-only), on the Raspberry Pi 5: oe-core builds GRUB
    and systemd-boot for every aarch64 wic image, whatever its partition
    layout, and the license check counts deployed bootloaders as part of the
    image. The Pi's firmware loads the kernel directly, so
    `kas/machine/raspberrypi5.yml` removes both from `WKS_FILE_DEPENDS`.
