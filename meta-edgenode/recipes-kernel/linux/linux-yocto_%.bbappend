# QEMU: vkms, a virtual KMS display, so the tests can drive edgenoded's
# display code without a panel (tests/dev/test_screen.py). A module that
# only the QEMU development image installs (kas/machine/qemuarm64.yml).
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append:qemuall = " file://vkms.cfg"
