# The Raspberry Pi 5's ADS1115: where edgenoded finds it and what is wired
# to which input (docs/lab.md).
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append:raspberrypi5 = " file://edgenoded-raspberrypi5.conf"

do_install:append:raspberrypi5() {
    { echo; cat ${S}/edgenoded-raspberrypi5.conf; } >> ${D}${sysconfdir}/edgenoded.conf
}
