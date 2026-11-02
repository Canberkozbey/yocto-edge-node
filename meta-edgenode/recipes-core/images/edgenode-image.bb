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

# License policy for the product: nothing under (L/A)GPLv3. The build of
# this image fails if such a package would be installed. Licenses with the
# GCC runtime exception (libgcc) are not affected. See docs/adr/0002.
INCOMPATIBLE_LICENSE = "GPL-3.0* LGPL-3.0* AGPL-3.0*"
