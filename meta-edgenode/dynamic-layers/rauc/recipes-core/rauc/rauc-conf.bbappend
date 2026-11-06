# The node's slot layout (files/<machine>/system.conf) and the keyring that
# verifies update bundles.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
PACKAGE_ARCH = "${MACHINE_ARCH}"

# The keyring is the CA certificate from the build host's key directory,
# outside the repository (docs/updates.md). The fetcher copies a file:// URL
# with an absolute path to the same path below UNPACKDIR.
inherit edgenode-keys
EDGENODE_KEYS_FILES = "ca.cert.pem"
RAUC_KEYRING_URI = "file://${EDGENODE_KEYS_DIR}/ca.cert.pem"
RAUC_KEYRING_FILE = "${@d.getVar('EDGENODE_KEYS_DIR').lstrip('/')}/ca.cert.pem"
do_fetch[prefuncs] += "edgenode_check_keys"
