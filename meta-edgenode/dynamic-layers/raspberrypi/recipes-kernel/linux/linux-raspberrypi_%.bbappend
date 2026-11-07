FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# dm-verity root filesystem without an initramfs (docs/adr/0009).
SRC_URI += "file://dm-verity-root.cfg"
