SUMMARY = "Init sequence for the edge node's ST7789 display"
DESCRIPTION = "The firmware file (panel.bin) with the commands the kernel's \
panel-mipi-dbi driver sends to the 1.3 inch 240x240 ST7789 display, compiled \
at build time from a commented text file."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://st7789-240x240.txt \
    file://mkpanelbin.py \
"
S = "${UNPACKDIR}"

inherit allarch python3native

do_compile() {
    ${PYTHON} ${S}/mkpanelbin.py ${S}/st7789-240x240.txt ${B}/panel.bin
}

do_install() {
    install -D -m 0644 ${B}/panel.bin ${D}${nonarch_base_libdir}/firmware/panel.bin
}

FILES:${PN} = "${nonarch_base_libdir}/firmware/panel.bin"
