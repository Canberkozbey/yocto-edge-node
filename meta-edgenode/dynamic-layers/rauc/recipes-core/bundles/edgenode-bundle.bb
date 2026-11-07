SUMMARY = "Edge node update bundle"
DESCRIPTION = "RAUC bundle with the production image's root filesystem, \
with its dm-verity hash tree, and the boot slot image that boots it: what an \
A/B update installs into the slot that is not running (docs/adr/0008, \
docs/adr/0009)."
LICENSE = "MIT"

COMPATIBLE_MACHINE = "^raspberrypi5$"

inherit bundle edgenode-keys

RAUC_BUNDLE_COMPATIBLE = "edgenode-${MACHINE}"
RAUC_BUNDLE_VERSION = "${DISTRO_VERSION}"
RAUC_BUNDLE_DESCRIPTION = "${SUMMARY}"
RAUC_BUNDLE_FORMAT = "verity"

# The image whose slots the bundle carries; both files come from its verity
# image type (edgenode-verity.bbclass), since the boot slot's command lines
# hold that root filesystem's root hash.
EDGENODE_BUNDLE_IMAGE ?= "edgenode-image"

RAUC_BUNDLE_SLOTS = "rootfs bootfs"
RAUC_SLOT_rootfs = "${EDGENODE_BUNDLE_IMAGE}"
RAUC_SLOT_rootfs[fstype] = "verity"
RAUC_SLOT_rootfs[rename] = "rootfs.img"
RAUC_SLOT_bootfs = "${EDGENODE_BUNDLE_IMAGE}"
RAUC_SLOT_bootfs[fstype] = "bootfs.vfat"
RAUC_SLOT_bootfs[rename] = "bootfs.vfat"

# Signed with the development key from the build host (docs/updates.md).
EDGENODE_KEYS_FILES = "dev.key.pem dev.cert.pem"
RAUC_KEY_FILE = "${EDGENODE_KEYS_DIR}/dev.key.pem"
RAUC_CERT_FILE = "${EDGENODE_KEYS_DIR}/dev.cert.pem"
do_bundle[prefuncs] += "edgenode_check_keys"
