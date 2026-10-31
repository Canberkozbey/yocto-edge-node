SUMMARY = "Edge node image"
DESCRIPTION = "Minimal systemd image for the edge node: boot essentials, \
SSH access for the integration tests, and the node's own services."
LICENSE = "MIT"

inherit core-image

IMAGE_INSTALL = "packagegroup-core-boot ${CORE_IMAGE_EXTRA_INSTALL}"
IMAGE_FEATURES += "ssh-server-openssh"
IMAGE_LINGUAS = ""
