# RAUC integration

The update system's recipes and bbappends: the slot configuration
(`rauc-conf`), the boot slot backend and commit service (`edgenode-boot`),
and the update bundles. `BBFILES_DYNAMIC` in `conf/layer.conf` parses this
directory only when meta-rauc (collection `rauc`) is part of the build.

Only the Raspberry Pi 5 build adds meta-rauc (`kas/machine/raspberrypi5.yml`),
and the slot layout and boot backend are the Pi's: the recipes here are
limited to that machine (`COMPATIBLE_MACHINE`, `files/raspberrypi5/`).
Design: [ADR 0008](../../../docs/adr/0008-ab-updates-with-rauc-and-tryboot.md);
keys and manual installation: [docs/updates.md](../../../docs/updates.md).
