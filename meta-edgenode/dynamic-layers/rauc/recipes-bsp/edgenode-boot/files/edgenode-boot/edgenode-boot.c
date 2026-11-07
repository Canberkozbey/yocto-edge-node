// SPDX-License-Identifier: MIT
/*
 * edgenode-boot: A/B boot slot control for the Raspberry Pi 5, through the
 * firmware's own mechanisms. RAUC calls it as its custom bootloader backend
 * (docs/adr/0008-ab-updates-with-rauc-and-tryboot.md).
 *
 * The SD card has two boot partitions (2 = slot A, 3 = slot B) and a small
 * first FAT partition with only autoboot.txt. The firmware reads it:
 *
 *   [all]
 *   tryboot_a_b=1
 *   boot_partition=2      <- the default slot, used on every normal boot
 *   [tryboot]
 *   boot_partition=3      <- the other slot, used once when tryboot is set
 *
 * The tryboot flag is a one-shot request to the firmware, set through its
 * mailbox and cleared by the next boot, by a power cut or by a watchdog
 * reset. So a new slot is booted once on trial, and only committing it
 * (rewriting autoboot.txt) makes it the default; any failure before that
 * falls back to the old slot.
 *
 * autoboot.txt is changed without mounting anything: the file's data sector
 * is found through the FAT structures and rewritten in place, with the same
 * length, in a single sector write. The FAT tables and the directory are
 * never written, so a power cut cannot corrupt the filesystem.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <linux/fs.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

#define AUTOBOOT_NAME "AUTOBOOTTXT" /* 8.3 directory entry name */
#define AUTOBOOT_TEMPLATE \
	"[all]\ntryboot_a_b=1\nboot_partition=%d\n[tryboot]\nboot_partition=%d\n"

/* Firmware mailbox property interface (drivers/char/broadcom/vcio.c). */
#define IOCTL_MBOX_PROPERTY _IOWR(100, 0, char *)
#define MBOX_TAG_GET_REBOOT_FLAGS 0x00030064
#define MBOX_TAG_SET_REBOOT_FLAGS 0x00038064
#define MBOX_SUCCESS 0x80000000
#define REBOOT_FLAG_TRYBOOT 0x1

enum slot { SLOT_A, SLOT_B, SLOT_NONE = -1 };

static struct {
	const char *autoboot; /* the FAT partition with autoboot.txt */
	const char *bootloader; /* the firmware's boot information */
	const char *state; /* where bad marks are kept */
	const char *mailbox; /* the firmware mailbox */
} cfg = {
	.autoboot = "/dev/mmcblk0p1",
	.bootloader = "/proc/device-tree/chosen/bootloader",
	.state = "/data/rauc",
	.mailbox = "/dev/vcio",
};

static void err(const char *fmt, ...)
{
	char line[512];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	fprintf(stderr, "edgenode-boot: %s\n", line);
}

static char slot_name(enum slot s)
{
	return s == SLOT_A ? 'A' : 'B';
}

static enum slot other(enum slot s)
{
	return s == SLOT_A ? SLOT_B : SLOT_A;
}

static int partition_of(enum slot s)
{
	return s == SLOT_A ? 2 : 3;
}

static enum slot slot_of_partition(long partition)
{
	return partition == 2 ? SLOT_A : partition == 3 ? SLOT_B : SLOT_NONE;
}

static enum slot parse_slot(const char *name)
{
	if (strcmp(name, "A") == 0)
		return SLOT_A;
	if (strcmp(name, "B") == 0)
		return SLOT_B;
	err("unknown slot '%s' (expected A or B)", name);
	return SLOT_NONE;
}

static uint16_t le16(const uint8_t *p)
{
	return p[0] | p[1] << 8;
}

static uint32_t le32(const uint8_t *p)
{
	return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
}

static int read_exact(int fd, void *buf, size_t len, off_t offset)
{
	ssize_t n = pread(fd, buf, len, offset);

	if (n < 0) {
		err("read at %lld: %s", (long long)offset, strerror(errno));
		return -1;
	}
	if ((size_t)n != len) {
		err("short read at %lld", (long long)offset);
		return -1;
	}
	return 0;
}

/* --- autoboot.txt on the FAT partition --------------------------------- */

struct fat_file {
	off_t sector_offset; /* byte offset of the file's first sector */
	unsigned sector_size;
	uint32_t size;
};

/*
 * Finds autoboot.txt in the root directory of a FAT12/16/32 filesystem and
 * the sector holding its data. Only what this tool needs: a file in the
 * root directory (within its first cluster on FAT32) that fits one sector.
 */
static int fat_locate(int fd, struct fat_file *file)
{
	uint8_t boot[512];
	uint8_t *dir = NULL;
	int ret = -1;

	if (read_exact(fd, boot, sizeof(boot), 0))
		return -1;
	if (boot[510] != 0x55 || boot[511] != 0xaa) {
		err("%s: no FAT boot sector", cfg.autoboot);
		return -1;
	}

	unsigned bps = le16(boot + 11);
	unsigned spc = boot[13];
	unsigned reserved = le16(boot + 14);
	unsigned fats = boot[16];
	unsigned root_entries = le16(boot + 17);
	uint32_t total = le16(boot + 19) ? le16(boot + 19) : le32(boot + 32);
	uint32_t fat_size = le16(boot + 22) ? le16(boot + 22) : le32(boot + 36);

	if (bps < 512 || bps > 4096 || (bps & (bps - 1)) || !spc ||
	    (spc & (spc - 1)) || !reserved || !fats || !fat_size) {
		err("%s: unsupported FAT geometry", cfg.autoboot);
		return -1;
	}

	uint32_t root_sectors = (root_entries * 32 + bps - 1) / bps;
	uint32_t first_data = reserved + fats * fat_size + root_sectors;
	if (first_data >= total) {
		err("%s: inconsistent FAT geometry", cfg.autoboot);
		return -1;
	}
	uint32_t clusters = (total - first_data) / spc;
	off_t dir_offset;
	size_t dir_len;

	if (clusters >= 65525) { /* FAT32: the root directory is a cluster chain */
		uint32_t root_cluster = le32(boot + 44);

		if (root_cluster < 2 || root_cluster >= clusters + 2) {
			err("%s: bad root directory cluster", cfg.autoboot);
			return -1;
		}
		dir_offset = ((off_t)first_data + (off_t)(root_cluster - 2) * spc) * bps;
		dir_len = (size_t)spc * bps;
	} else { /* FAT12/16: a fixed region before the data area */
		dir_offset = ((off_t)reserved + (off_t)fats * fat_size) * bps;
		dir_len = (size_t)root_sectors * bps;
	}

	dir = malloc(dir_len);
	if (!dir) {
		err("out of memory");
		return -1;
	}
	if (read_exact(fd, dir, dir_len, dir_offset))
		goto out;

	for (size_t i = 0; i + 32 <= dir_len; i += 32) {
		const uint8_t *e = dir + i;
		uint8_t attr = e[11];

		if (e[0] == 0x00)
			break; /* end of directory */
		if (e[0] == 0xe5 || attr == 0x0f || (attr & 0x18))
			continue; /* deleted, long name part, directory or label */
		if (memcmp(e, AUTOBOOT_NAME, 11) != 0)
			continue;

		uint32_t cluster = (uint32_t)le16(e + 20) << 16 | le16(e + 26);
		file->size = le32(e + 28);
		file->sector_size = bps;
		if (cluster < 2 || cluster >= clusters + 2) {
			err("%s: autoboot.txt has a bad first cluster", cfg.autoboot);
			goto out;
		}
		if (file->size == 0 || file->size > bps) {
			err("%s: autoboot.txt is %u bytes; expected one sector at most",
			    cfg.autoboot, file->size);
			goto out;
		}
		file->sector_offset = ((off_t)first_data + (off_t)(cluster - 2) * spc) * bps;
		ret = 0;
		goto out;
	}
	err("%s: no autoboot.txt in the root directory", cfg.autoboot);
out:
	free(dir);
	return ret;
}

struct autoboot {
	int fd;
	struct fat_file file;
	uint8_t sector[4096];
	enum slot default_slot;
};

/* The default slot is the only part of autoboot.txt that may differ from
 * the template; anything else means someone changed the file by hand, and
 * this tool refuses to touch it. */
static enum slot parse_autoboot(const uint8_t *data, uint32_t size)
{
	char expected[256];

	for (enum slot s = SLOT_A; s <= SLOT_B; s++) {
		int len = snprintf(expected, sizeof(expected), AUTOBOOT_TEMPLATE,
				   partition_of(s), partition_of(other(s)));
		if ((uint32_t)len == size && memcmp(data, expected, size) == 0)
			return s;
	}
	return SLOT_NONE;
}

static int autoboot_open(struct autoboot *ab, int writable)
{
	ab->fd = open(cfg.autoboot, (writable ? O_RDWR : O_RDONLY) | O_CLOEXEC);
	if (ab->fd < 0) {
		err("%s: %s", cfg.autoboot, strerror(errno));
		return -1;
	}
	/* Serializes read-modify-write cycles of concurrent invocations. */
	if (flock(ab->fd, writable ? LOCK_EX : LOCK_SH) < 0) {
		err("%s: lock: %s", cfg.autoboot, strerror(errno));
		goto fail;
	}
	if (fat_locate(ab->fd, &ab->file))
		goto fail;
	if (read_exact(ab->fd, ab->sector, ab->file.sector_size, ab->file.sector_offset))
		goto fail;
	ab->default_slot = parse_autoboot(ab->sector, ab->file.size);
	if (ab->default_slot == SLOT_NONE) {
		err("%s: autoboot.txt is not in the expected format", cfg.autoboot);
		goto fail;
	}
	return 0;
fail:
	close(ab->fd);
	return -1;
}

static void autoboot_close(struct autoboot *ab)
{
	close(ab->fd);
}

/* Rewrites autoboot.txt so that `slot` is the default: one sector, written
 * through, flushed and read back from the card. */
static int autoboot_set_default(struct autoboot *ab, enum slot slot)
{
	char text[256];
	uint8_t check[4096];
	unsigned bps = ab->file.sector_size;
	int len;

	if (ab->default_slot == slot)
		return 0;
	len = snprintf(text, sizeof(text), AUTOBOOT_TEMPLATE,
		       partition_of(slot), partition_of(other(slot)));
	if ((uint32_t)len != ab->file.size) {
		err("internal error: autoboot.txt would change size");
		return -1;
	}
	memcpy(ab->sector, text, len);
	if (pwrite(ab->fd, ab->sector, bps, ab->file.sector_offset) != (ssize_t)bps) {
		err("%s: write: %s", cfg.autoboot, strerror(errno));
		return -1;
	}
	if (fsync(ab->fd) < 0) {
		err("%s: sync: %s", cfg.autoboot, strerror(errno));
		return -1;
	}
	/* Drop the cached copy so that the check reads the card itself. Only
	 * block devices have that cache; a regular file (tests) has none. */
	if (ioctl(ab->fd, BLKFLSBUF, 0) < 0 && errno != ENOTTY) {
		err("%s: flush buffers: %s", cfg.autoboot, strerror(errno));
		return -1;
	}
	if (read_exact(ab->fd, check, bps, ab->file.sector_offset))
		return -1;
	if (memcmp(check, ab->sector, bps) != 0) {
		err("%s: autoboot.txt reads back differently", cfg.autoboot);
		return -1;
	}
	ab->default_slot = slot;
	fprintf(stderr, "edgenode-boot: slot %c is now the default\n", slot_name(slot));
	return 0;
}

/* --- firmware state ---------------------------------------------------- */

/* The slot the firmware booted, from the partition it loaded files from. */
static enum slot booted_slot(void)
{
	char path[512];
	uint8_t be[4];
	int fd;

	snprintf(path, sizeof(path), "%s/partition", cfg.bootloader);
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return SLOT_NONE;
	if (read(fd, be, sizeof(be)) != sizeof(be)) {
		close(fd);
		return SLOT_NONE;
	}
	close(fd);
	return slot_of_partition((long)((uint32_t)be[0] << 24 | be[1] << 16 | be[2] << 8 | be[3]));
}

static int mailbox(uint32_t tag, uint32_t *value)
{
	uint32_t msg[8] = { sizeof(msg), 0, tag, 4, 0, *value, 0, 0 };
	int fd = open(cfg.mailbox, O_RDONLY | O_CLOEXEC);

	if (fd < 0) {
		err("%s: %s", cfg.mailbox, strerror(errno));
		return -1;
	}
	if (ioctl(fd, IOCTL_MBOX_PROPERTY, msg) < 0) {
		err("%s: mailbox call 0x%08x: %s", cfg.mailbox, tag, strerror(errno));
		close(fd);
		return -1;
	}
	close(fd);
	if (msg[1] != MBOX_SUCCESS || !(msg[4] & MBOX_SUCCESS)) {
		err("firmware rejected mailbox call 0x%08x", tag);
		return -1;
	}
	*value = msg[5];
	return 0;
}

static int tryboot_get(int *armed)
{
	uint32_t flags = 0;

	if (mailbox(MBOX_TAG_GET_REBOOT_FLAGS, &flags))
		return -1;
	*armed = !!(flags & REBOOT_FLAG_TRYBOOT);
	return 0;
}

static int tryboot_set(int armed)
{
	uint32_t flags = armed ? REBOOT_FLAG_TRYBOOT : 0;

	return mailbox(MBOX_TAG_SET_REBOOT_FLAGS, &flags);
}

/* --- marks ------------------------------------------------------------ */

/* Empty files in the state directory, one per slot and kind:
 *
 *   bad-<slot>    the slot is marked bad: what `rauc status` shows
 *   trial-<slot>  the slot is armed for a trial boot whose outcome nobody
 *                 has recorded yet
 *
 * Booting itself is controlled only by autoboot.txt and the tryboot flag.
 * A trial mark still there when the other slot runs means the trial boot
 * never got as far as the commit service: power cut, hang, panic. */

enum mark { MARK_BAD, MARK_TRIAL };

static void mark_path(char *path, size_t len, enum mark kind, enum slot s)
{
	snprintf(path, len, "%s/%s-%c", cfg.state, kind == MARK_BAD ? "bad" : "trial",
		 slot_name(s));
}

static int sync_state_dir(void)
{
	int fd = open(cfg.state, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	int ret = 0;

	if (fd < 0 || fsync(fd) < 0) {
		err("%s: sync: %s", cfg.state, strerror(errno));
		ret = -1;
	}
	if (fd >= 0)
		close(fd);
	return ret;
}

static int is_marked(enum mark kind, enum slot s)
{
	char path[512];

	mark_path(path, sizeof(path), kind, s);
	return access(path, F_OK) == 0;
}

static int mark(enum mark kind, enum slot s)
{
	char path[512];
	int fd;

	if (mkdir(cfg.state, 0700) < 0 && errno != EEXIST) {
		err("%s: %s", cfg.state, strerror(errno));
		return -1;
	}
	mark_path(path, sizeof(path), kind, s);
	fd = open(path, O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
	if (fd < 0) {
		err("%s: %s", path, strerror(errno));
		return -1;
	}
	close(fd);
	return sync_state_dir();
}

static int unmark(enum mark kind, enum slot s)
{
	char path[512];

	mark_path(path, sizeof(path), kind, s);
	if (unlink(path) < 0) {
		if (errno == ENOENT)
			return 0;
		err("%s: %s", path, strerror(errno));
		return -1;
	}
	return sync_state_dir();
}

/* --- commands ---------------------------------------------------------- */

/* The slot the next boot will use. */
static int cmd_get_primary(void)
{
	struct autoboot ab;
	int armed;

	if (autoboot_open(&ab, 0))
		return 1;
	autoboot_close(&ab);
	if (tryboot_get(&armed))
		return 1;
	printf("%c\n", slot_name(armed ? other(ab.default_slot) : ab.default_slot));
	return 0;
}

/* Makes `slot` the slot of the next boot: the default slot as it is, the
 * other one on trial, through the tryboot flag. */
static int cmd_set_primary(enum slot slot)
{
	struct autoboot ab;
	int trial;

	if (autoboot_open(&ab, 0))
		return 1;
	autoboot_close(&ab);
	trial = slot != ab.default_slot;
	if (tryboot_set(trial))
		return 1;
	if (trial ? mark(MARK_TRIAL, slot) : unmark(MARK_TRIAL, other(slot)))
		return 1;
	return unmark(MARK_BAD, slot) ? 1 : 0;
}

static int cmd_get_state(enum slot slot)
{
	printf("%s\n", is_marked(MARK_BAD, slot) ? "bad" : "good");
	return 0;
}

static int cmd_set_state(enum slot slot, const char *state)
{
	enum slot booted = booted_slot();
	struct autoboot ab;
	int ret = 1;

	if (strcmp(state, "good") != 0 && strcmp(state, "bad") != 0) {
		err("unknown state '%s' (expected good or bad)", state);
		return 1;
	}
	if (autoboot_open(&ab, 1))
		return 1;

	/* Either way, the outcome of any trial of this slot is now recorded. */
	if (unmark(MARK_TRIAL, slot))
		goto out;

	if (strcmp(state, "good") == 0) {
		/* Committing the booted slot after its trial boot. */
		if (slot == booted && autoboot_set_default(&ab, slot))
			goto out;
		ret = unmark(MARK_BAD, slot) ? 1 : 0;
		goto out;
	}

	if (slot == ab.default_slot) {
		if (booted != other(slot)) {
			/* The committed slot is also the running one: marking
			 * it bad would leave nothing known to work to boot. */
			err("slot %c is booted and committed; refusing to mark it bad",
			    slot_name(slot));
			goto out;
		}
		/* Slot `slot` is about to change (an update installs into
		 * it) while the booted slot is still on trial. The running
		 * slot becomes the fallback first. */
		if (autoboot_set_default(&ab, booted))
			goto out;
	} else if (tryboot_set(0)) {
		/* No trial boot of a slot that is bad or being written. */
		goto out;
	}
	ret = mark(MARK_BAD, slot) ? 1 : 0;
out:
	autoboot_close(&ab);
	return ret;
}

static int cmd_get_current(void)
{
	enum slot booted = booted_slot();

	if (booted == SLOT_NONE) {
		err("not booted from slot A or B (%s/partition)", cfg.bootloader);
		return 1;
	}
	printf("%c\n", slot_name(booted));
	return 0;
}

/* Prints the slot whose trial boot ended without a verdict, exit status 0;
 * exit status 1 when there is none. */
static int cmd_failed_trial(void)
{
	enum slot booted = booted_slot();

	for (enum slot s = SLOT_A; s <= SLOT_B; s++) {
		if (s != booted && is_marked(MARK_TRIAL, s)) {
			printf("%c\n", slot_name(s));
			return 0;
		}
	}
	return 1;
}

/* Exit status 0 when the booted slot is on trial (not yet the default). */
static int cmd_is_trial(void)
{
	enum slot booted = booted_slot();
	struct autoboot ab;

	if (booted == SLOT_NONE)
		return 1;
	if (autoboot_open(&ab, 0))
		return 2;
	autoboot_close(&ab);
	return booted != ab.default_slot ? 0 : 1;
}

static int cmd_status(void)
{
	enum slot booted = booted_slot();
	struct autoboot ab;
	int armed;

	if (autoboot_open(&ab, 0))
		return 1;
	autoboot_close(&ab);
	printf("default:  %c\n", slot_name(ab.default_slot));
	if (booted == SLOT_NONE)
		printf("booted:   neither slot\n");
	else
		printf("booted:   %c%s\n", slot_name(booted),
		       booted != ab.default_slot ? " (trial)" : "");
	if (tryboot_get(&armed) == 0)
		printf("next:     %c%s\n",
		       slot_name(armed ? other(ab.default_slot) : ab.default_slot),
		       armed ? " (trial)" : "");
	for (enum slot s = SLOT_A; s <= SLOT_B; s++)
		printf("slot %c:   %s%s\n", slot_name(s), is_marked(MARK_BAD, s) ? "bad" : "good",
		       is_marked(MARK_TRIAL, s) ? ", trial pending" : "");
	return 0;
}

static void usage(FILE *out)
{
	fprintf(out,
		"Usage: edgenode-boot [OPTIONS] COMMAND\n"
		"\n"
		"RAUC custom bootloader backend commands:\n"
		"  get-primary              slot of the next boot\n"
		"  set-primary A|B          boot this slot next (on trial if not the default)\n"
		"  get-state A|B            good or bad\n"
		"  set-state A|B good|bad   good on the booted slot commits it\n"
		"  get-current              booted slot\n"
		"Other commands:\n"
		"  status                   summary of the above\n"
		"  is-trial                 exit status 0 if the booted slot is on trial\n"
		"  failed-trial             print a slot whose trial boot ended without a verdict\n"
		"\n"
		"Options:\n"
		"  --autoboot DEVICE        FAT partition with autoboot.txt (%s)\n"
		"  --bootloader DIRECTORY   firmware boot information (%s)\n"
		"  --state DIRECTORY        bad and trial marks (%s)\n"
		"  --mailbox DEVICE         firmware mailbox (%s)\n",
		cfg.autoboot, cfg.bootloader, cfg.state, cfg.mailbox);
}

int main(int argc, char **argv)
{
	static const struct option options[] = {
		{ "autoboot", required_argument, NULL, 'a' },
		{ "bootloader", required_argument, NULL, 'b' },
		{ "state", required_argument, NULL, 's' },
		{ "mailbox", required_argument, NULL, 'm' },
		{ "help", no_argument, NULL, 'h' },
		{ NULL, 0, NULL, 0 },
	};
	int opt;

	while ((opt = getopt_long(argc, argv, "+h", options, NULL)) != -1) {
		switch (opt) {
		case 'a': cfg.autoboot = optarg; break;
		case 'b': cfg.bootloader = optarg; break;
		case 's': cfg.state = optarg; break;
		case 'm': cfg.mailbox = optarg; break;
		case 'h': usage(stdout); return 0;
		default: usage(stderr); return 2;
		}
	}
	argc -= optind;
	argv += optind;
	if (argc < 1) {
		usage(stderr);
		return 2;
	}

	const char *cmd = argv[0];
	enum slot slot = SLOT_NONE;

	if (!strcmp(cmd, "set-primary") || !strcmp(cmd, "get-state") ||
	    !strcmp(cmd, "set-state")) {
		if (argc != (strcmp(cmd, "set-state") ? 2 : 3)) {
			usage(stderr);
			return 2;
		}
		slot = parse_slot(argv[1]);
		if (slot == SLOT_NONE)
			return 2;
	} else if (argc != 1) {
		usage(stderr);
		return 2;
	}

	if (!strcmp(cmd, "get-primary"))
		return cmd_get_primary();
	if (!strcmp(cmd, "set-primary"))
		return cmd_set_primary(slot);
	if (!strcmp(cmd, "get-state"))
		return cmd_get_state(slot);
	if (!strcmp(cmd, "set-state"))
		return cmd_set_state(slot, argv[2]);
	if (!strcmp(cmd, "get-current"))
		return cmd_get_current();
	if (!strcmp(cmd, "is-trial"))
		return cmd_is_trial();
	if (!strcmp(cmd, "failed-trial"))
		return cmd_failed_trial();
	if (!strcmp(cmd, "status"))
		return cmd_status();
	err("unknown command '%s'", cmd);
	usage(stderr);
	return 2;
}
