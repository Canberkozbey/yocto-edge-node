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
  Raspberry Pi 5, `kas/machine/raspberrypi5.yml` switches both radios off
  with the `disable-wifi-pi5` and `disable-bt-pi5` device tree overlays and
  keeps the Wi-Fi and Bluetooth firmware the machine recommends out of the
  images.
- **Kernel modules are installed one by one.** The images do not install
  the BSP's `kernel-modules` recommendation (it comes with
  `packagegroup-base`, which they do not use), so a module is on the device
  only when it is listed with its function, as the ADC's are.
- Second application, once the hardware set was final: **no VC4 graphics**
  on the Raspberry Pi 5. The display is an SPI panel with its own DRM
  driver (ADR 0006), and nothing on the node uses HDMI, the V3D GPU or
  sound. `kas/machine/raspberrypi5.yml` sets `DISABLE_VC4GRAPHICS`, which
  drops the BSP's `vc4graphics` machine feature:
  - the `vc4-kms-v3d` overlay leaves `config.txt`, so the hardware stays
    off;
  - the kernel no longer has the DRM core, the VC4 driver and the sound
    core built in (the BSP's `vc4graphics.cfg`). All three stay modules,
    as in the kernel's own configuration, and the images install only the
    DRM modules the panel driver depends on.
- Third application: **one log, the journal.** `packagegroup-core-boot`
  recommends a syslog daemon, BusyBox's by default
  (`VIRTUAL-RUNTIME_base-utils-syslog`). Next to journald it took
  `/dev/log` over, so whatever logged through syslog, sshd's logins among
  them, went to BusyBox's log and never reached the journal. `edgenode.conf`
  empties the variable, so no syslog daemon is installed, and journald
  does not forward to one (`ForwardToSyslog=no`).
- `tests/image/test_artifacts.py` checks that no wireless firmware and no
  syslog daemon is installed and, on the Raspberry Pi 5, that both radio
  overlays are in `config.txt` and no VC4 overlay is. On the board,
  `tests/dev/test_display.py` checks that no VC4 or sound driver registers
  and that the panel is the only DRM device. On every machine,
  `tests/dev/test_boot.py` checks that an SSH login shows up in the
  journal.

## Alternatives considered

- **Removing `wifi`, `bluetooth` and other unused features (`alsa`, `3g`,
  `nfc`, `zeroconf`, `nfs`, `debuginfod`) from `DISTRO_FEATURES`:** not
  done. They change how packages are built rather than what the images
  install, and keeping Poky's defaults avoids an untested combination.
- **Leaving the radios on and only omitting the firmware:** the hardware
  would still be enabled with no use for it.
- **Only removing the VC4 overlay:** the hardware would be off, but the VC4
  driver and the sound core would still be in the kernel.

## Consequences

- Wi-Fi or Bluetooth can come back with a function that needs them: undo the
  firmware removal and the overlays, and record why in a new ADR.
- A display on HDMI would bring VC4 back: drop `DISABLE_VC4GRAPHICS` and
  record why.
- Logs leave the node only through journald. A log server for a fleet
  would take them from there (`systemd-journal-upload`, or a forwarder
  that reads the journal) and comes with its own ADR.
