SUMMARY = "Boot slot template and autoboot partition images for the Raspberry Pi 5"
DESCRIPTION = "The FAT image of a boot slot (firmware configuration, kernel, \
device trees, overlays), made from the BSP's boot files, and the small first \
partition whose autoboot.txt selects the slot. Each image adds its kernel \
command lines, which carry its root filesystem's root hash \
(edgenode-verity.bbclass); the SD card image and the RAUC update bundles use \
that boot slot image (docs/adr/0008, docs/adr/0009)."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://autoboot.txt"
S = "${UNPACKDIR}"
B = "${WORKDIR}/build"

COMPATIBLE_MACHINE = "^raspberrypi5$"
PACKAGE_ARCH = "${MACHINE_ARCH}"
INHIBIT_DEFAULT_DEPS = "1"

inherit deploy nopackages

DEPENDS = "dosfstools-native mtools-native"

# Sizes in KiB, equal to the partitions in the SD card's layout
# (wic/sdimage-raspberrypi5-ab.wks.in): an update rewrites the whole slot.
EDGENODE_BOOTFS_SIZE ?= "131072"
EDGENODE_AUTOBOOT_SIZE ?= "16384"

# The boot files: what the BSP's own SD card image would contain
# (IMAGE_BOOT_FILES, conf/machine/include/rpi-base.inc in meta-raspberrypi,
# plus our overlay from kas/machine/raspberrypi5.yml), from the same deploy
# tasks.
do_compile[depends] += " \
    virtual/kernel:do_deploy \
    rpi-bootfiles:do_deploy \
    edgenode-dt-overlays:do_deploy \
"
do_compile[vardeps] += "IMAGE_BOOT_FILES"
do_compile[cleandirs] = "${B}"

# Copies the boot files into ${B}/bootfs, read the way wic's
# bootimg-partition plugin reads IMAGE_BOOT_FILES: entries are "src" or
# "src;dst", src relative to DEPLOY_DIR_IMAGE; a glob installs each match
# under its base name (into dst, if given). Then makes the FAT images.
python do_compile() {
    import glob
    import shutil

    deploy = d.getVar('DEPLOY_DIR_IMAGE')
    stage = os.path.join(d.getVar('B'), 'bootfs')

    installs = []
    for entry in d.getVar('IMAGE_BOOT_FILES').split():
        src, _, dst = entry.partition(';')
        if '*' in src:
            matches = sorted(glob.glob(os.path.join(deploy, src)))
            if not matches:
                bb.fatal("IMAGE_BOOT_FILES: nothing matches %s" % src)
            for match in matches:
                name = os.path.basename(match)
                installs.append((match, os.path.join(dst, name) if dst else name))
        else:
            installs.append((os.path.join(deploy, src), dst or src))

    for src, dst in installs:
        target = os.path.join(stage, dst)
        if os.path.exists(target):
            bb.fatal("IMAGE_BOOT_FILES installs %s twice" % dst)
        bb.utils.mkdirhier(os.path.dirname(target))
        shutil.copyfile(src, target)

    bb.build.exec_func('edgenode_make_images', d)
}

edgenode_make_images() {
    # Reproducible: fixed volume ID and serial, file times from
    # SOURCE_DATE_EPOCH, FAT times in UTC.
    export TZ=UTC MTOOLS_SKIP_CHECK=1
    find bootfs -exec touch -h -d @${SOURCE_DATE_EPOCH} {} +
    touch -d @${SOURCE_DATE_EPOCH} ${UNPACKDIR}/autoboot.txt

    mkfs.vfat --invariant -n BOOT -C bootfs.vfat ${EDGENODE_BOOTFS_SIZE}
    mcopy -i bootfs.vfat -s -p -m bootfs/* ::/

    # FAT16: the firmware reads FAT16 and FAT32, and the partition is too
    # small for FAT32.
    mkfs.vfat --invariant -F 16 -n AUTOBOOT -C autoboot.vfat ${EDGENODE_AUTOBOOT_SIZE}
    mcopy -i autoboot.vfat -p -m ${UNPACKDIR}/autoboot.txt ::/autoboot.txt
}
edgenode_make_images[dirs] = "${B}"

do_deploy() {
    install -m 0644 ${B}/bootfs.vfat ${DEPLOYDIR}/${PN}-${MACHINE}.vfat
    install -m 0644 ${B}/autoboot.vfat ${DEPLOYDIR}/edgenode-autoboot-${MACHINE}.vfat
}
addtask deploy after do_compile before do_build
