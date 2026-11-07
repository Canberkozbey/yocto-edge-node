# Root filesystem verified by dm-verity, and the boot slot image that boots
# it (docs/adr/0009-read-only-root-with-dm-verity.md). Inherited by the
# images through IMAGE_CLASSES (kas/machine/raspberrypi5.yml).
#
# Image type "verity" writes three files next to the image's ext4:
#
#   <image>.verity       the ext4, with its hash tree appended: what an A/B
#                        root filesystem slot holds
#   <image>.verity.env   the parameters the kernel needs to check it
#   <image>.bootfs.vfat  the boot slot image (edgenode-bootfs) with one kernel
#                        command line per slot that sets the root filesystem
#                        up through dm-verity, with this image's root hash
#
# The kernel creates the verity device itself, from dm-mod.create= on its
# command line (CONFIG_DM_INIT), so no initramfs is needed. Every block read
# from the root filesystem is checked against the hash tree, whose root hash
# is on the command line; a changed block reads as an I/O error.

IMAGE_TYPES += "verity"
IMAGE_TYPEDEP:verity = "ext4"

# The root filesystem partition of each slot (wic/sdimage-raspberrypi5-ab.wks.in).
EDGENODE_ROOT_A ?= "/dev/mmcblk0p4"
EDGENODE_ROOT_B ?= "/dev/mmcblk0p5"
EDGENODE_VERITY_BLOCK_SIZE = "4096"

do_image_verity[depends] += " \
    cryptsetup-native:do_populate_sysroot \
    mtools-native:do_populate_sysroot \
    edgenode-bootfs:do_deploy \
"
do_image_verity[vardeps] += "EDGENODE_ROOT_A EDGENODE_ROOT_B"

# The verity device's sectors are as large as its data blocks, and ext4
# does not mount with blocks smaller than a sector ("bad block size"). mke2fs
# picks 1 KiB blocks for a file system this small unless it is told.
EXTRA_IMAGECMD:ext4:append = " -b ${EDGENODE_VERITY_BLOCK_SIZE}"

IMAGE_CMD:verity () {
	export TZ=UTC MTOOLS_SKIP_CHECK=1
	ext4="${IMGDEPLOYDIR}/${IMAGE_NAME}.ext4"
	verity="${IMGDEPLOYDIR}/${IMAGE_NAME}.verity"
	boot="${IMGDEPLOYDIR}/${IMAGE_NAME}.bootfs.vfat"
	bs=${EDGENODE_VERITY_BLOCK_SIZE}

	# expr, not $((...)): bitbake's shell parser does not take arithmetic.
	size=$(stat -L -c %s "$ext4")
	if [ $(expr $size % $bs) -ne 0 ]; then
		bbfatal "$ext4 is $size bytes, not a whole number of $bs-byte blocks"
	fi
	blocks=$(expr $size / $bs)
	# Salted with the data's own digest: reproducible, and different for
	# every image.
	salt=$(sha256sum "$ext4" | cut -d' ' -f1)

	# The hash tree goes right after the data, without a verity superblock:
	# the kernel gets every parameter on its command line.
	#
	# Copies keep the holes of their source and make no new ones: never
	# --sparse=always. The SD card image keeps holes as holes, and writers
	# that follow its block map (bmaptool, tests/update) skip them, so the
	# card keeps whatever it held there before. A hole is only safe where
	# nothing reads it.
	cp "$ext4" "$verity"
	veritysetup format --no-superblock --hash=sha256 \
		--data-block-size=$bs --hash-block-size=$bs \
		--data-blocks=$blocks --hash-offset=$size --salt=$salt \
		"$verity" "$verity" > "$verity.format"
	roothash=$(sed -n 's/^Root hash:[[:space:]]*//p' "$verity.format")
	rm "$verity.format"
	if [ -z "$roothash" ]; then
		bbfatal "veritysetup printed no root hash"
	fi
	cat > "$verity.env" <<EOF
VERITY_DATA_BLOCKS=$blocks
VERITY_BLOCK_SIZE=$bs
VERITY_HASH_OFFSET=$size
VERITY_SALT=$salt
VERITY_ROOT_HASH=$roothash
EOF

	# The boot slot image: the BSP's boot files, plus a command line per
	# slot. config.txt picks the file by the partition the firmware booted
	# from. The command line built by the BSP names a plain root partition;
	# root= is replaced by the verity device over that slot's partition.
	cp "${DEPLOY_DIR_IMAGE}/edgenode-bootfs-${MACHINE}.vfat" "$boot"
	cmdline=$(mtype -i "$boot" ::cmdline.txt)
	case " $cmdline " in
	*" root="*) ;;
	*) bbfatal "cmdline.txt has no root= argument: $cmdline" ;;
	esac
	sectors=$(expr $size / 512)
	work="${WORKDIR}/verity-cmdline"
	rm -rf "$work"
	mkdir -p "$work"
	for slot in A B; do
		if [ $slot = A ]; then dev=${EDGENODE_ROOT_A}; else dev=${EDGENODE_ROOT_B}; fi
		# <start> <sectors> verity <version> <data dev> <hash dev>
		# <data block size> <hash block size> <data blocks> <hash start block>
		# <algorithm> <root hash> <salt> <number of options> <options>
		#
		# ignore_zero_blocks: a block whose hash says it is all zeros reads
		# as zeros, without reading the partition. The ext4 image leaves
		# its unused blocks as holes, which the SD card keeps from before
		# (see the copies above); a stray read of one, readahead say, then
		# finds zeros rather than a verification error. Every other block
		# is still read and checked.
		table="0 $sectors verity 1 $dev $dev $bs $bs $blocks $blocks sha256 $roothash $salt 1 ignore_zero_blocks"
		verityargs="dm-mod.create=\"vroot,,,ro,$table\" dm-mod.waitfor=$dev root=/dev/dm-0 ro"
		echo "$cmdline" | sed "s|root=[^ ]*|$verityargs|" | sed "s|\$| rauc.slot=$slot|" \
			> "$work/cmdline-$slot.txt"
		touch -d @${SOURCE_DATE_EPOCH} "$work/cmdline-$slot.txt"
		mcopy -o -p -m -i "$boot" "$work/cmdline-$slot.txt" ::cmdline-$slot.txt
	done

	# Image types get their link (<image link name>.<type>) automatically;
	# the two extra files get theirs here, unless the names are the same
	# (IMAGE_VERSION_SUFFIX empty).
	if [ "${IMAGE_NAME}" != "${IMAGE_LINK_NAME}" ]; then
		ln -sf "${IMAGE_NAME}.verity.env" "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.verity.env"
		ln -sf "${IMAGE_NAME}.bootfs.vfat" "${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.bootfs.vfat"
	fi
}
