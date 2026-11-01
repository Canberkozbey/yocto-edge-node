SUMMARY = "Edge node configuration"
DESCRIPTION = "Product configuration for edgenoded, packaged apart from the \
daemon so a machine or product variant can change it without rebuilding \
the binary."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://edgenoded.conf"
S = "${UNPACKDIR}"

# Nothing to compile: don't wait for the cross toolchain.
INHIBIT_DEFAULT_DEPS = "1"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

do_install() {
    install -D -m 0644 ${S}/edgenoded.conf ${D}${sysconfdir}/edgenoded.conf
}

# Keep local edits on package upgrades.
CONFFILES:${PN} = "${sysconfdir}/edgenoded.conf"

# Machine-specific bbappends (dynamic-layers) may change the values.
PACKAGE_ARCH = "${MACHINE_ARCH}"
