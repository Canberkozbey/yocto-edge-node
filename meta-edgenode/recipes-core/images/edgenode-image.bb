SUMMARY = "Edge node production image"
DESCRIPTION = "Minimal systemd image for the edge node: boot essentials, \
an SSH server and the node's own services. The root account is locked and \
there is no password login; edgenode-image-dev adds development access."
LICENSE = "MIT"

inherit core-image

IMAGE_INSTALL = " \
    packagegroup-core-boot \
    edgenoded \
    ${CORE_IMAGE_EXTRA_INSTALL} \
"
IMAGE_FEATURES += "ssh-server-openssh"
IMAGE_LINGUAS = ""
