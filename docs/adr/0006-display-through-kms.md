# ADR 0006: The display through DRM/KMS, without libdrm

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

The node shows its readings on the 240x240 ST7789 panel. The kernel already
drives the panel: `panel-mipi-dbi` registers it as a DRM device and, through
the kernel's fbdev emulation, as a framebuffer that the console draws on.
`edgenoded` needs to draw a few lines of text on it, once a second at most.
The images carry no graphics stack (ADR 0003, and
`tests/image/test_artifacts.py` checks it).

## Decision

- `edgenoded` draws through KMS: one dumb buffer in XRGB8888 (the format
  every KMS driver accepts), the connector's preferred mode set with the
  legacy `SETCRTC` call, and `DIRTYFB` after each redraw. Panels without a
  scanout engine of their own, such as this SPI panel, send the buffer over
  the bus on `DIRTYFB`; drivers that scan out continuously answer `ENOSYS`,
  which is not an error.
- The KMS calls are made with the kernel's uapi headers (`drm/drm_mode.h`,
  from linux-libc-headers), not libdrm: about 350 lines (`display.c`), no
  extra package on the device.
- The output is configured by connector name as `/sys/class/drm` shows it
  (`display_connector = SPI-1`); the card and the connector's object ID come
  from sysfs. The panel's driver can load after the daemon starts, so the
  daemon opens the output once it appears, and again after an error. The
  screen is redrawn once a second, and sent to the panel only when what it
  shows has changed.
- Opening the card makes the daemon the DRM master, so the console stays
  off the panel while the daemon runs and gets it back when it stops.
- The font is Spleen (12x24 and 16x32, BSD-2-Clause), fetched by the recipe
  at a pinned commit and converted to C at build time (`bdf2c.py`). The
  kernel's own fonts are GPL-2.0 and would make the daemon GPL. The
  recipe's license is `MIT & BSD-2-Clause`, so the license manifest and the
  image's license files carry Spleen's notice.
- Device access only where there is a display (ADR 0003): the base unit
  keeps `PrivateDevices=yes`. On the Raspberry Pi 5 a systemd drop-in, from
  the same bbappend that sets `display_connector`, replaces it with a closed
  device policy that allows DRM devices (`DeviceAllow=char-drm rw`) and the
  `video` group that owns their nodes.

## Testing

- On the Raspberry Pi 5 the panel cannot be read back. debugfs
  (`/sys/kernel/debug/dri/<N>/state`) names the process whose framebuffer
  is on screen, so `tests/dev/test_display.py` checks that it is
  `edgenoded`, in the panel's mode, and that the console takes the panel
  back when the service stops.
- In QEMU, vkms (a virtual KMS display, built as a module for the QEMU
  development image only) stands in for the panel, so CI exercises the
  display code on every pull request. vkms computes a CRC of each frame it
  composes: `tests/dev/test_screen.py` checks that the CRC holds still
  while nothing changes (the control) and changes after a reading does.
- What the screen looks like is a manual check (docs/lab.md).

## Alternatives considered

- **fbdev (`/dev/fb0`):** less code, but it is the kernel's compatibility
  layer over DRM, gives no say over modes, and shares the panel with the
  console without any handover.
- **libdrm:** the usual way, with a friendlier API. For the handful of
  calls a single text screen needs it adds a package and little else; it
  becomes worth it with atomic mode setting or several planes.
- **Atomic mode setting:** needed for tear-free page flips and planes. A
  text screen redrawn at most once a second needs neither; the kernel
  turns the legacy calls into atomic commits for atomic drivers.
- **A UI toolkit (LVGL, Qt):** a graphics stack for a few lines of text.

## Consequences

- One buffer: on a display that scans out continuously, a redraw could
  tear. On the SPI panel the transfer happens inside `DIRTYFB`, while the
  daemon is not drawing.
- One display, printable ASCII only.
- On the Raspberry Pi 5 the service sees the names of all device nodes
  (`PrivateDevices=no`), though it can open only DRM devices and the basic
  pseudo devices. `systemd-analyze security` rates its exposure a little
  higher there.
