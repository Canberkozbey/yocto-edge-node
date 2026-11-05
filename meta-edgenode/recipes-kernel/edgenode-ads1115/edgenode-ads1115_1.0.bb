SUMMARY = "Kernel driver for the edge node's ADS1115 ADC"
DESCRIPTION = "Out-of-tree IIO driver for the TI ADS1115: four single-ended \
inputs, single-shot reads, full-scale range from the device tree. Packaged \
as kernel-module-edgenode-ads1115; see docs/adr/0004."
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

inherit module

SRC_URI = "file://edgenode-ads1115"
S = "${UNPACKDIR}/edgenode-ads1115"
