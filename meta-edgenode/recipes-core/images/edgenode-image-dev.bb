require edgenode-image.bb

SUMMARY = "Edge node development image"
DESCRIPTION = "The production image plus development access: root logs in \
without a password on the console and over SSH, and gdbserver, strace and \
i2c-tools are installed. Never ship this image."

IMAGE_FEATURES += "allow-empty-password empty-root-password allow-root-login"

# Remote debugging from the host's cross gdb; no gdb (and Python) on target.
# i2c-tools for bringing up I2C devices by hand.
IMAGE_INSTALL:append = " gdbserver strace i2c-tools"

# The license policy is for the product image only; gdbserver is GPLv3.
INCOMPATIBLE_LICENSE = ""
