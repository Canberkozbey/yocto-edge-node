# ADR 0009: Read-only root filesystem verified by dm-verity

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

With A/B updates (ADR 0008) a new root filesystem only arrives as a signed
bundle. Once a slot is written, nothing checks it again. Someone with the SD
card, a process that gains root, or a failing card can change the root
filesystem, and the node runs whatever is there. A writable root also drifts
from the image it was built as, so the bytes in the field are not the bytes
that were tested.

## Decision

- **The root filesystem is read-only and verified by dm-verity.** Every
  block read from it is hashed and checked against a hash tree, up to a root
  hash. A changed block reads as an I/O error and never as changed data.
- **The kernel sets the verity device up itself, without an initramfs.**
  `dm-mod.create=` on the kernel command line describes the device:
  - the slot's partition as data and hash device;
  - the block counts;
  - the root hash and the salt.

  `root=/dev/dm-0 ro` mounts it. The Pi kernel gets device mapper, its early
  setup (`CONFIG_DM_INIT`), the verity target and SHA-256 built in, through
  a configuration fragment
  (`dynamic-layers/raspberrypi/recipes-kernel/linux`).
- **Each image hashes itself.** `edgenode-verity.bbclass` adds the image
  type `verity`, which takes the image's ext4 and does three things:
  - `veritysetup` from cryptsetup-native appends the hash tree right after
    the data, without a verity superblock. The salt is the data's SHA-256,
    so the result is reproducible and different for every image. The ext4
    has 4 KiB blocks, the verity block size: the verity device's sectors
    are that large, and ext4 does not mount with smaller blocks.
  - It writes the boot slot image with one kernel command line per slot,
    each with that root hash and that slot's partition.
  - It writes the parameters to `<image>.verity.env`.

  The SD card's slot A and the update bundles carry exactly these two
  files. A slot's boot partition and root filesystem therefore always
  belong together, which RAUC's slot groups already ensure.
- **The image runs read-only** (`IMAGE_FEATURES` `read-only-rootfs`). Logs,
  `/var/lib` and the rest of the state are on tmpfs, through oe-core's
  volatile binds. Units that write to `/var/lib` before `local-fs.target`
  (screen backlight, networkd's persistent storage) wait for that bind
  through drop-ins in `systemd-conf`; without them they race it and fail.
  RAUC mounts bundles under `/run/rauc` and keeps its status on `/data`.
  The data partition's directories are made by a unit tied to its mount
  (`edgenode-data.service`).
- **Holes in the SD card image are never read.** The SD card image keeps
  the holes of what it is made of, and tools that write it by its block map
  (`bmaptool`, `tests/update`) skip them, so a used card keeps its old data
  there. So:
  - copies made while building keep their source's holes and make no new
    ones, which leaves holes only in unused space, never inside a boot
    file;
  - the verity table has `ignore_zero_blocks`: a block that the hash tree
    says is all zeros reads as zeros without being read from the card. The
    unused blocks of the ext4, which are holes, stay out of any read;
    every other block is still checked.
- **No error mode beyond the default.** A corrupted block returns an I/O
  error to whoever reads it. During a trial boot, that ends in a failed
  mount, a failed service or a panic, and the board returns to the previous
  slot (ADR 0008). On a committed slot it shows as failing reads. A
  `restart_on_corruption` loop on the only slot known to work would be
  worse.
- **A trial boot that never reports back is recorded.** `edgenode-boot`
  marks a slot as on trial when it arms the tryboot flag. If the other slot
  then runs and the mark is still there, the trial never reached the commit
  service (power cut, hang, panic, a root filesystem that fails
  verification). `edgenode-boot-commit` then marks that slot bad, so
  `rauc status` shows what happened.

## Testing

- **`tests/image/test_updates.py`**, without booting:
  - each image's verity file passes `veritysetup verify` against the root
    hash on its boot slot's command line;
  - its data area is the image's ext4, byte for byte, with blocks as large
    as the verity blocks;
  - each slot's command line names its own partition and the read-only
    mount;
  - each boot slot, read as a used card holds it after a block map write
    (old data everywhere the block map leaves out), still has every boot
    file byte for byte;
  - the bundles carry the verity file and that boot slot image;
  - the images are configured for a read-only root.
- **`tests/update/` on the Raspberry Pi 5:**
  - `/` is the verity device over the booted slot's partition, mounted
    read-only. Writing to it and remounting it read-write both fail.
  - Before a trial boot, one block of the installed root filesystem is
    zeroed. The trial boot's kernel cannot mount it, panics and reboots, the
    board comes back on the previous slot, and RAUC shows the tampered slot
    as bad.
  - The failed-release test now breaks the release through its boot slot's
    command line (masking edgenoded). Its root filesystem can no longer be
    changed without failing verification.

## Alternatives considered

- **An initramfs that runs `veritysetup`:** the usual way, and the way to
  go once there is more to do before mounting the root (a signed root hash,
  an encrypted data partition). For a known layout it is one more image to
  build, sign and update, for work the kernel does itself.
- **Signed root hash** (`CONFIG_DM_VERITY_VERIFY_ROOTHASH_SIG`, a key in the
  kernel's keyring): protects the root hash on the unsigned boot partition.
  Without secure boot, though, the kernel and its keyring sit on that same
  partition, so it adds nothing yet (below).
- **meta-security's `dm-verity-img.bbclass`:** built around an initramfs.
  Another layer for one class.
- **A read-only mount without verification:** stops drift, not tampering.
- **dm-verity's forward error correction:** repairs a few bad blocks of an
  ageing card, but adds size and complexity; an update rewrites the slot
  anyway.

## Consequences

- **No secure boot yet.** The boot partitions are not verified. Someone
  with the SD card can change a kernel command line, root hash included, and
  boot a modified root filesystem. dm-verity guarantees that the root
  filesystem matches the root hash the boot partition names. Closing the
  rest needs the Pi 5's signed boot (`boot.img` signed with a key whose hash
  is in OTP). That step is irreversible and deliberately not taken on this
  lab board.
- **Everything that must persist goes to `/data`:** RAUC's status, and
  since ADR 0011 the SSH host keys, the site's configuration and the node's
  MQTT identity.
- **Configuration changes need a new image.** `/etc` is part of the
  verified root filesystem.
- **The `cmdline.txt` fallback** for a bootloader without
  `[boot_partition]` support mounts slot A without verification. The update
  tests require a bootloader that has it.
- **Network boot** serves the same image over NFS, without verification. It
  is for the lab only.
