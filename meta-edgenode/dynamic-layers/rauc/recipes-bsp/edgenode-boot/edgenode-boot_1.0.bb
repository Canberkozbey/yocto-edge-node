SUMMARY = "A/B boot slot control for the Raspberry Pi 5, RAUC's bootloader backend"
DESCRIPTION = "edgenode-boot selects the boot slot through the Raspberry Pi \
firmware's autoboot.txt and its one-shot tryboot flag; RAUC calls it as its \
custom bootloader backend. After each boot from a slot, \
edgenode-boot-commit checks the node's service and commits the slot or \
returns to the previous one. Also mounts the persistent data partition."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://edgenode-boot"
S = "${UNPACKDIR}/edgenode-boot"

# Tryboot, the mailbox call and the SD card layout are the Raspberry Pi 5's
# (docs/adr/0008).
COMPATIBLE_MACHINE = "^raspberrypi5$"
PACKAGE_ARCH = "${MACHINE_ARCH}"

do_install() {
    oe_runmake install \
        DESTDIR=${D} \
        BINDIR=${bindir} \
        LIBEXECDIR=${libexecdir} \
        UNITDIR=${systemd_system_unitdir} \
        GENERATORDIR=${systemd_unitdir}/system-generators
}

# The units are not enabled: edgenode-boot-generator adds them to boots from
# an A/B slot only. /data is the mount point, shipped in the image rather
# than created at boot.
FILES:${PN} += " \
    ${systemd_system_unitdir} \
    ${systemd_unitdir}/system-generators \
    /data \
"

RDEPENDS:${PN} = "rauc"
