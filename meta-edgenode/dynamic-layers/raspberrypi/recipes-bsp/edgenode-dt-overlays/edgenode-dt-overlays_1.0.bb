SUMMARY = "Device tree overlays for the edge node's Raspberry Pi 5 hardware"
DESCRIPTION = "Overlays for hardware on the Pi's 40-pin header that the edge \
node's own drivers handle. Deployed for the boot partition's overlays/ \
directory (IMAGE_BOOT_FILES, kas/machine/raspberrypi5.yml)."
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

inherit devicetree

COMPATIBLE_MACHINE = "^raspberrypi5$"

SRC_URI = "file://edgenode-ads1115.dts"
S = "${UNPACKDIR}"
