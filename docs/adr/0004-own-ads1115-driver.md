# ADR 0004: Own ADS1115 driver, validated against the mainline driver

- **Status:** Accepted
- **Date:** 2026-11-04

## Context

The node reads analog inputs through a TI ADS1115. Mainline Linux supports
the chip (`ti-ads1015`): differential inputs, buffered capture, threshold
events, runtime power management. This project also sets out to show kernel
driver work. A driver for hardware that is already on the board, with a
mature driver to compare against, is the most testable way to do that.

## Decision

- `edgenode-ads1115` is an out-of-tree IIO driver, small by design: four
  single-ended inputs, one single-shot conversion per sysfs read (the
  converter powers down in between), one full-scale range for all channels
  from the device tree (`edgenode,full-scale-mv`), and a probe that fails
  when nothing answers at the address.
- For what both drivers do, it offers the same interface as the mainline
  one: `in_voltageN_raw`, and `in_voltageN_scale` in millivolts. The IIO
  device names differ (the mainline driver names its device `ads1015` for
  every chip it supports, ours `ads1115`), so `tests/dev/test_adc.py` finds
  the device by its I2C address and runs unchanged against either.
- It has its own compatible (`edgenode,ads1115`) and overlay
  (`edgenode-ads1115`), so the two drivers never compete for the device;
  the `dtoverlay` line in `config.txt` picks one.
- Both drivers stay in the images until ours passes the tests on the
  hardware with the same readings as the mainline driver. Then the mainline
  driver and its overlay leave the images (ADR 0003). Done; see Validation.
- `module.bbclass` builds the driver (`kernel-module-edgenode-ads1115`);
  `devicetree.bbclass` builds the overlay, which goes into the boot
  partition's `overlays/` directory.

## Validation

On 2026-11-05, on the lab's Raspberry Pi 5, with the potentiometer left in
place between the runs, `tests/dev/test_adc.py` passed with each driver, and
the raw readings (0.125 mV per count) agreed within a few counts:

| Input | Own driver | Mainline driver | Difference |
| --- | --- | --- | --- |
| A0, potentiometer | 16520 | 16507 | 13 counts (1.6 mV) |
| A1, GND | 3 | 2 | 1 count |
| A2, 3.3 V | 26164 | 26170 | 6 counts (0.75 mV) |

The comparison also caught a test bug: the test first looked the device up
by name and so found only our driver's. With that fixed and the readings in
agreement, the mainline driver and its overlay left the images.

## Alternatives considered

- **Mainline driver only:** what a product would normally do, with nothing
  extra to maintain. The own driver exists to show driver work; the
  comparison with the mainline driver keeps it honest.
- **Reading the chip from user space over i2c-dev:** simpler, but it
  bypasses the kernel's interfaces. The node's software reads IIO, so it
  works with either kernel driver.

## Consequences

- Out of tree, the driver has to follow kernel API changes. It is built
  with `W=1` and kept clean under `checkpatch.pl --strict`.
- The `edgenode` vendor prefix is not registered in the kernel's device
  tree bindings: acceptable for an out-of-tree driver, not for upstreaming.
- Not covered: differential inputs, buffered capture, threshold events.
