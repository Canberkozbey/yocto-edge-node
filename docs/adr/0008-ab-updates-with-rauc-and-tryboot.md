# ADR 0008: A/B updates with RAUC and the firmware's tryboot

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

The node has to be updated remotely, with nobody on site, and must never
end up unable to boot. A power cut during an update, a release whose
service does not start, or a hang while the new version boots must each
leave a working system that can be updated again.

On the Raspberry Pi 5 the bootloader in the board's EEPROM and its firmware
load the kernel directly. This build has no U-Boot (stage 2). The firmware
has its own A/B support: `autoboot.txt` on the first FAT partition names the
partition to boot (`boot_partition`). With `tryboot_a_b=1`, a one-shot
"tryboot" flag makes the next boot use the partition from the file's
`[tryboot]` section instead. The firmware clears the flag on that boot.
A power cut or a watchdog reset clears it as well.

## Decision

- **RAUC** is the update client, from meta-rauc. It brings signed bundles,
  a slot model with slot groups, and a D-Bus API that an update agent can
  drive later. Only the Raspberry Pi 5 build adds the layer, and its
  recipes and bbappends live in `meta-edgenode/dynamic-layers/rauc`.
- **Symmetric A/B slots on the SD card.** Each slot has a boot partition
  (firmware configuration, kernel, device trees, overlays) and a root
  filesystem. The boot partition is a child of the root filesystem, so the
  two are always installed and booted together. A data partition outside
  the slots survives updates.

  | Partition | GPT name | Size | Contents |
  | --- | --- | --- | --- |
  | 1 | autoboot | 16 MiB | FAT16, `autoboot.txt` only |
  | 2 | bootA | 128 MiB | FAT, slot A's boot files (`bootfs.0`) |
  | 3 | bootB | 128 MiB | FAT, slot B's boot files (`bootfs.1`) |
  | 4 | rootA | 1 GiB | slot A's root filesystem (`rootfs.0`): ext4 and its dm-verity hash tree (ADR 0009) |
  | 5 | rootB | 1 GiB | slot B's root filesystem (`rootfs.1`), the same |
  | 6 | data | 256 MiB | ext4, `/data`: RAUC's status, the slots' bad marks |

  The partition numbers are written down in the wks file, `system.conf`,
  `edgenode-boot`, `edgenode-verity.bbclass` and `data.mount`.
  `tests/image/test_updates.py` checks that the built images agree with
  them.
- **The firmware chooses the slot.** Committing a slot means rewriting
  `autoboot.txt` so that it becomes the default. An installed update boots
  once on trial, through the tryboot flag. Until it is committed, any reset
  returns to the slot that worked before.
- **Our own RAUC backend, `edgenode-boot`** (C, about 700 lines), connects
  RAUC to this. RAUC's `custom` backend interface calls it for
  `get-primary`, `set-primary`, `get-state`, `set-state` and `get-current`.
  It does three things:
  - It rewrites `autoboot.txt` without mounting anything. It finds the
    file's data sector through the FAT structures and rewrites that one
    sector in place, with text of the same length. It then flushes, drops
    the cache and reads the sector back. The FAT tables and the directory
    are never written.
  - It sets and reads the tryboot flag through the firmware's mailbox
    (`/dev/vcio`).
  - It refuses changes that would leave nothing known to work: marking the
    committed, running slot bad, or overwriting the fallback while the
    running slot is still on trial (it makes the running slot the default
    first).
- **One kernel command line per slot.** Both boot partitions get the same
  image, as built and as shipped in the bundle. `config.txt` picks
  `cmdline-A.txt` or `cmdline-B.txt` by the partition the firmware booted
  from (`[boot_partition=N]`, bootloader EEPROM 2025-03-10 or later). Each
  names its own root filesystem and `rauc.slot`. `cmdline.txt` stays as
  the fallback, mounting slot A, and network boot rewrites it.
  `edgenode-bootfs` builds the boot files from the BSP's `IMAGE_BOOT_FILES`,
  so the slot holds exactly the files the BSP's own image would. Each image
  adds the two command lines, which carry its root hash (ADR 0009).
- **A health check decides the commit.** `edgenode-boot-commit` runs after
  each boot from a slot. A systemd generator adds it and the data
  partition's mount only to such boots: had they been enabled with a
  condition, a network boot would still wait 90 s for an SD card partition
  that may not exist. It waits until edgenoded, the node's service,
  reports ready to systemd and then runs 15 s without a restart. Then it
  calls `rauc status mark-good`, which commits a slot on trial. If a trial
  boot is unhealthy after 60 s, it calls `rauc status mark-bad` and
  reboots, and the previous slot starts again. `panic=10` turns a kernel
  panic into a reboot.
- **Hardware watchdog.** systemd feeds the SoC's watchdog (15 s), so a hang
  of the kernel or of systemd resets the board. A reset also clears the
  tryboot flag.
- **Verity bundles only, signed.** The bundle payload is checked block by
  block as it is read during installation (`bundle-formats=verity`).
  Bundles are signed with ECDSA P-256. The signing certificate must chain
  to the CA certificate in the image and must be meant for code signing
  (`check-purpose=codesign`). Its validity is checked at the bundle's
  signing time, because a board without an RTC battery can boot with the
  wrong time.
- **Keys stay outside the repository.** `tools/make-signing-keys.sh dev`
  creates development keys on the build host; release keys live in CI's
  secrets (ADR 0010). The build container gets the
  directory with the signing key and the CA certificate read-only, at
  `/keys`; the CA key stays out of it. A missing key fails the build with a
  message that says what to do (`edgenode-keys.bbclass`).
- **Only what the function needs** (ADR 0003). RAUC is built with its
  D-Bus service and JSON output, without download, streaming or GPT
  support. The kernel's squashfs and dm-verity modules mount bundles, so
  squashfs-tools and `rauc-mark-good` are left out.

## Testing

- **`tests/image/test_updates.py`** checks the build output without
  booting it:
  - the partition table of both SD card images;
  - `autoboot.txt`;
  - each slot's kernel command line and the `config.txt` section that
    picks it;
  - that each bundle carries, byte for byte, the root filesystem and boot
    slot image of this build;
  - RAUC's configuration and package set in the images.
- **`tests/update/` on the Raspberry Pi 5** writes the SD card from the
  development image booted over the network, then boots the card and runs,
  in order:
  1. checks the EEPROM version, the slot A boot and the armed watchdog;
  2. installs an update, boots it on trial and commits it;
  3. cuts the power before the trial boot, and the board stays on the old
     slot;
  4. installs a slot whose edgenoded fails, and the board comes back to the
     previous slot by itself, as it does from a slot whose root filesystem
     fails verification (ADR 0009);
  5. cuts the power during installation, and the board stays on the old
     slot, which is then updated cleanly;
  6. checks that bundles with a broken signature or payload are rejected;
  7. makes the kernel panic with automatic reboot off, and the hardware
     watchdog resets the board.
- **`edgenode-boot`** was also exercised on FAT12, FAT16 and FAT32 images
  on the build host, with a stand-in for the mailbox, under ASan and UBSan.
  The run covered every slot transition above.

## Alternatives considered

- **U-Boot with boot counting:** RAUC and meta-rauc support it out of the
  box. But it adds a bootloader to build, maintain and secure on a board
  whose firmware already does the A/B part. The tryboot flag does what a
  boot counter with one try does.
- **The firmware's file-level tryboot** (`tryboot.txt` and a second kernel
  in one boot partition): not symmetric, and every update rewrites files
  the running system boots from.
- **Mender or SWUpdate:** Mender brings its own client and server model,
  which this project does not need. SWUpdate would fit as well as RAUC.
  RAUC won for its slot groups, verity bundles, D-Bus API and a maintained
  layer for this Yocto release.
- **Rewriting `autoboot.txt` through a mounted filesystem or mtools:**
  mtools and dosfstools are GPLv3 (ADR 0002). A vfat mount writes FAT
  metadata, which a power cut can corrupt, and needs its modules in the
  image.
- **`vcmailbox` from raspi-utils for the flag:** pulls in GnuTLS for one
  ioctl.
- **Device tree or kernel command line parameters from the firmware to
  pick the root filesystem** (`root=PARTUUID` patched per slot at install
  time): the boot slot image would no longer be the same bytes in both
  slots and in the bundle.

## Consequences

- **What survives a failure.** A power cut at any point of an update
  leaves the previous slot. A release that does not come up healthy is
  rolled back without anyone on site.
- **Firmware features.** The design relies on `autoboot.txt` with
  `tryboot_a_b`, on the mailbox's reboot flags, and on the
  `[boot_partition]` filter (EEPROM 2025-03-10 or later). The update tests
  check each on the real board.
- **Hangs before the watchdog is armed.** A trial boot that hangs before
  systemd starts feeding the watchdog stays hung until a power cycle. That
  power cycle clears the flag and returns to the previous slot.
- **No secure boot.** Anyone with the SD card can change any slot's boot
  partition. Signed bundles protect the update path, and dm-verity protects
  the root filesystem against any change its boot partition does not name
  (ADR 0009). Signed boot on the Pi 5 would need its
  OTP programmed, which is irreversible and deliberately not done on this
  lab board.
- **Full images.** Bundles carry whole slot images; delta updates (RAUC's
  adaptive updates, casync) could come later.
- **Delivery is local.** Delivery is out of scope here: the tests copy the
  bundle to the board. A fleet would use an update server (hawkBit with
  `rauc-hawkbit-updater`) or RAUC's streaming over HTTPS.
- **Development keys.** Anyone with them can sign updates for images built
  with them. Releases are signed with separate release keys from CI's
  secret store (ADR 0010); a fleet would keep them in an HSM.
