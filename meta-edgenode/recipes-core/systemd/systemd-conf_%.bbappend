FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

SRC_URI += "file://journald-edgenode.conf"

# Sorts after 00-systemd-conf.conf, so these settings win.
do_install:append() {
    install -D -m 0644 ${S}/journald-edgenode.conf \
        ${D}${systemd_unitdir}/journald.conf.d/10-edgenode.conf
}
