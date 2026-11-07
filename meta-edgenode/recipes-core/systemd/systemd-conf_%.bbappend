FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

SRC_URI += " \
    file://journald-edgenode.conf \
    file://after-volatile-var-lib.conf \
"

# Units that write to /var/lib early in the boot, before local-fs.target
# (after-volatile-var-lib.conf).
EDGENODE_AFTER_VOLATILE_VAR_LIB = " \
    systemd-backlight@.service \
    systemd-networkd-persistent-storage.service \
"

# Sorts after 00-systemd-conf.conf, so these settings win.
do_install:append() {
    install -D -m 0644 ${S}/journald-edgenode.conf \
        ${D}${systemd_unitdir}/journald.conf.d/10-edgenode.conf
    for unit in ${EDGENODE_AFTER_VOLATILE_VAR_LIB}; do
        install -D -m 0644 ${S}/after-volatile-var-lib.conf \
            ${D}${systemd_system_unitdir}/$unit.d/10-edgenode-volatile.conf
    done
}

FILES:${PN} += "${@' '.join('${systemd_system_unitdir}/%s.d/' % unit for unit in d.getVar('EDGENODE_AFTER_VOLATILE_VAR_LIB').split())}"
