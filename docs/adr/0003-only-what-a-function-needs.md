# ADR 0003: Only what a function needs on the device

- **Status:** Accepted
- **Date:** 2026-11-02

## Context

Every package, firmware file and enabled hardware block on the device has to
be maintained, updated and secured, and will show up in the SBOM and CVE
reports planned for later stages. Board support layers default to a
general-purpose setup: the Raspberry Pi 5 machine configuration, for
example, installs Wi-Fi and Bluetooth firmware for every image.

## Decision

- The images contain only what serves a function of the node. Whoever adds
  something names that function, in the commit or in an ADR.
- The rule applies to what is installed and which hardware is enabled. The
  distro's feature set stays as Poky defines it, apart from the headless
  removals already in `edgenode.conf`. Staying close to Poky's widely tested
  defaults is worth more than trimming build options that install nothing
  by themselves.
- First application: the node uses **wired Ethernet only**. On the
  Raspberry Pi 5, `kas/machine/raspberrypi5.yml` leaves out the Wi-Fi and
  Bluetooth firmware the machine recommends, and switches both radios off
  with the `disable-wifi-pi5` and `disable-bt-pi5` device tree overlays.
- `tests/image/test_artifacts.py` checks that no wireless firmware is
  installed and, on the Raspberry Pi 5, that both overlays are in
  `config.txt`.

## Alternatives considered

- **Removing `wifi`, `bluetooth` and other unused features (`alsa`, `3g`,
  `nfc`, `zeroconf`, `nfs`, `debuginfod`) from `DISTRO_FEATURES`:** not
  done. They change how packages are built rather than what the images
  install, and keeping Poky's defaults avoids an untested combination.
- **Leaving the radios on and only omitting the firmware:** the hardware
  would still be enabled with no use for it.

## Consequences

- Wi-Fi or Bluetooth can come back with a function that needs them: undo the
  firmware removal and the overlays, and record why in a new ADR.
- Not yet applied, to revisit once the hardware set is final (ADC driver
  and display): the BSP installs every kernel module (`kernel-modules`) and
  enables the VC4 graphics overlay.
