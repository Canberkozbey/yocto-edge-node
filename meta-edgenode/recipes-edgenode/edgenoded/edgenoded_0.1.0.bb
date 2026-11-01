SUMMARY = "Edge node daemon"
DESCRIPTION = "Serves the node's health over HTTP and reports readiness and \
liveness to systemd. Later stages add sensor readings and MQTT publishing."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://edgenoded"
S = "${UNPACKDIR}/edgenoded"

inherit systemd

EXTRA_OEMAKE = "VERSION=${PV}"

do_install() {
    oe_runmake install \
        DESTDIR=${D} \
        BINDIR=${bindir} \
        UNITDIR=${systemd_system_unitdir}
}

SYSTEMD_SERVICE:${PN} = "edgenoded.service"

# The daemon runs on built-in defaults; the config package sets them for the
# product and can differ per machine.
RRECOMMENDS:${PN} = "edgenode-config"
