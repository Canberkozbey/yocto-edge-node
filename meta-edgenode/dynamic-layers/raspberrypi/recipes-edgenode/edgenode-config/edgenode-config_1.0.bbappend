# The Raspberry Pi 5's ADS1115 and ST7789: where edgenoded finds them and
# what is wired to which input (docs/lab.md), and the device access drawing
# on the panel takes. Other machines keep the base unit's PrivateDevices=.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append:raspberrypi5 = " file://edgenoded-raspberrypi5.conf file://edgenoded-display.conf"

do_install:append:raspberrypi5() {
    { echo; cat ${S}/edgenoded-raspberrypi5.conf; } >> ${D}${sysconfdir}/edgenoded.conf
    install -D -m 0644 ${S}/edgenoded-display.conf \
        ${D}${systemd_system_unitdir}/edgenoded.service.d/display.conf
}

FILES:${PN}:append:raspberrypi5 = " ${systemd_system_unitdir}/edgenoded.service.d"
