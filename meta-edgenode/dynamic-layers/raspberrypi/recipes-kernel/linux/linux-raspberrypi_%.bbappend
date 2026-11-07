FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# dm-verity root filesystem without an initramfs (docs/adr/0009).
SRC_URI += "file://dm-verity-root.cfg"
# The source files the kernel builds, for the SBOM and the CVE report
# (docs/adr/0010). In every build, so the kernel the report covers is the
# kernel the images ship.
SRC_URI += "file://debug-info.cfg"
