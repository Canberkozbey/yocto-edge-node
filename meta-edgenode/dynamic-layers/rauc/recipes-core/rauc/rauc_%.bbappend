# Only what the node uses (docs/adr/0003): the D-Bus service and JSON status
# output. Bundles arrive as local files, so no downloading or streaming
# (curl, libnl), and no GPT partition switching (util-linux).
PACKAGECONFIG = "service json nocreate"

# Bundles are mounted with the kernel's squashfs; the user-space tools are
# not needed. edgenode-boot-commit marks slots good or bad, in place of
# rauc-mark-good (docs/adr/0008).
RRECOMMENDS:${PN}:remove = "squashfs-tools ${PN}-mark-good"
