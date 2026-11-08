SUMMARY = "Edge node daemon"
DESCRIPTION = "Serves the node's health and analog sensor readings (from an \
ADC through IIO) over HTTP, shows the readings on a display through DRM/KMS, \
publishes them over MQTT, and reports readiness and liveness to systemd."
# The daemon is MIT. The screen font compiled into it is Spleen, under
# BSD-2-Clause, whose notice ships with the image's license files.
LICENSE = "MIT & BSD-2-Clause"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302 \
                    file://${UNPACKDIR}/spleen/LICENSE;md5=6b0faaab001f2e05d12e136feb82cf71"

SRC_URI = "file://edgenoded \
           git://github.com/fcambus/spleen.git;protocol=https;branch=master;name=spleen;destsuffix=spleen"
# Spleen 2.2.0
SRCREV_spleen = "0493c34e22791824767c618fed42c434b477662c"
S = "${UNPACKDIR}/edgenoded"

# libmosquitto, from meta-networking (ADR 0007), with TLS. OpenSSL directly
# for the reason a broker's certificate failed verification (ADR 0011).
DEPENDS = "mosquitto openssl"

# python3: bdf2c.py converts the BDF fonts to C at build time.
inherit systemd python3native useradd

# The daemon's supplementary group, the only one that reads the site
# directory on /data: the site's configuration and the node's MQTT identity
# (edgenoded-site, docs/adr/0011).
USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "--system edgenode-identity"

EXTRA_OEMAKE = "VERSION=${PV} SPLEEN=${UNPACKDIR}/spleen"

do_install() {
    oe_runmake install \
        DESTDIR=${D} \
        BINDIR=${bindir} \
        LIBEXECDIR=${libexecdir} \
        UNITDIR=${systemd_system_unitdir}
}

SYSTEMD_SERVICE:${PN} = "edgenoded.service"
# edgenoded-site.service is not enabled: edgenoded.service pulls it in. The
# systemd class packages only the units it enables, so this one is listed.
FILES:${PN} += "${systemd_system_unitdir}/edgenoded-site.service"

# The daemon runs on built-in defaults; the config package sets them for the
# product and can differ per machine.
RRECOMMENDS:${PN} = "edgenode-config"
