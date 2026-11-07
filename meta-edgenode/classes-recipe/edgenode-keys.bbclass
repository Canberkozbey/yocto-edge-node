# Signing keys for update bundles, and the keyring that verifies them.
#
# They are never part of the repository. tools/make-signing-keys.sh creates
# them on the build host, and the build container sees their directory,
# read-only, at EDGENODE_KEYS_DIR (docs/updates.md). Release builds get the
# release keys from CI's secrets instead (docs/releases.md). A recipe
# lists the files it reads in EDGENODE_KEYS_FILES and runs
# edgenode_check_keys before the task that reads them, which turns a missing
# key into an error that says what to do.

EDGENODE_KEYS_DIR ??= "/keys"
EDGENODE_KEYS_FILES ??= ""

python edgenode_check_keys() {
    keys = d.getVar('EDGENODE_KEYS_DIR')
    missing = [name for name in d.getVar('EDGENODE_KEYS_FILES').split()
               if not os.path.isfile(os.path.join(keys, name))]
    if missing:
        bb.fatal("Update signing files not found in %s: %s. Create development "
                 "keys with tools/make-signing-keys.sh dev and give the build container "
                 "their directory (docs/updates.md)." % (keys, ' '.join(missing)))
}
