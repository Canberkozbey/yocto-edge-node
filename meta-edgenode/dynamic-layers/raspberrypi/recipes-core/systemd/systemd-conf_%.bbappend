FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

SRC_URI += "file://watchdog-edgenode.conf"

# Sorts after 00-systemd-conf.conf, so these settings win.
do_install:append() {
    install -D -m 0644 ${S}/watchdog-edgenode.conf \
        ${D}${systemd_unitdir}/system.conf.d/10-watchdog-edgenode.conf
}
