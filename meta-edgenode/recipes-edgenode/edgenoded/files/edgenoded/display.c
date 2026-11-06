// SPDX-License-Identifier: MIT
/*
 * A drawing surface on a DRM/KMS output, set up with the kernel's
 * mode-setting ioctls directly: find the connector, pick its preferred mode
 * and a CRTC, create a dumb buffer, map it and set the mode.
 *
 * Panels without a scanout engine of their own (SPI panels such as the
 * ST7789) send the buffer over the bus when told to with DIRTYFB. Drivers
 * that scan out continuously do not implement that call and answer ENOSYS.
 *
 * Opening the card's node makes this process the DRM master if no other
 * process is. While it is, the kernel's console stays off this output;
 * closing the node gives the output back to the console.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <drm/drm.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_mode.h>

#include "display.h"

#define CONNECTOR_NAME_MAX 32

/* enum drm_connector_status, which the uapi headers do not export */
#define CONNECTOR_DISCONNECTED 2

static int xioctl(int fd, unsigned long request, void *arg)
{
	int ret;

	do
		ret = ioctl(fd, request, arg);
	while (ret < 0 && (errno == EINTR || errno == EAGAIN));
	return ret;
}

__attribute__((format(printf, 3, 4)))
static int fail(char *err, size_t len, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(err, len, fmt, ap);
	va_end(ap);
	return -1;
}

int display_connector_valid(const char *connector)
{
	size_t len = strlen(connector);

	return len > 0 && len < CONNECTOR_NAME_MAX &&
	       strspn(connector, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
				 "abcdefghijklmnopqrstuvwxyz0123456789-") == len;
}

/* "card<N>-<connector>": the card part must be digits ("card*-A-1" also
 * matches card0-HDMI-A-1). Copies "card<N>" to card. */
static bool connector_dir_matches(const char *dir, const char *connector,
				  char *card, size_t card_len)
{
	const char *base = strrchr(dir, '/');
	size_t digits;

	base = base ? base + 1 : dir;
	if (strncmp(base, "card", 4) != 0)
		return false;
	digits = strspn(base + 4, "0123456789");
	if (digits == 0 || base[4 + digits] != '-' ||
	    strcmp(base + 4 + digits + 1, connector) != 0 ||
	    4 + digits >= card_len)
		return false;
	memcpy(card, base, 4 + digits);
	card[4 + digits] = '\0';
	return true;
}

static int read_u32_file(const char *path, uint32_t *value)
{
	char text[32];
	unsigned long v;
	ssize_t n;
	char *end;
	int fd;

	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -1;
	n = read(fd, text, sizeof(text) - 1);
	close(fd);
	if (n <= 0)
		return -1;
	text[n] = '\0';
	errno = 0;
	v = strtoul(text, &end, 10);
	if (errno || end == text || (*end != '\n' && *end != '\0') || v > UINT32_MAX)
		return -1;
	*value = (uint32_t)v;
	return 0;
}

/* The card ("card1") and KMS object ID of a connector, from sysfs. */
static int find_connector(struct display *d, const char *connector,
			  char *err, size_t err_len)
{
	char pattern[64], path[PATH_MAX];
	unsigned int matches = 0;
	glob_t found;

	snprintf(pattern, sizeof(pattern), "/sys/class/drm/card*-%s", connector);
	if (glob(pattern, 0, NULL, &found) != 0) {
		globfree(&found);
		return fail(err, err_len, "no display connector %s", connector);
	}
	for (size_t i = 0; i < found.gl_pathc; i++) {
		if (!connector_dir_matches(found.gl_pathv[i], connector,
					   d->card, sizeof(d->card)))
			continue;
		snprintf(path, sizeof(path), "%s/connector_id", found.gl_pathv[i]);
		matches++;
	}
	globfree(&found);

	if (matches != 1)
		return fail(err, err_len, "%s display connector %s",
			    matches ? "more than one" : "no", connector);
	if (read_u32_file(path, &d->connector_id) < 0)
		return fail(err, err_len, "cannot read %s", path);
	return 0;
}

/* A CRTC that can drive the connector: the one it is on, or the first free
 * one of its encoders. */
static int find_crtc(struct display *d, const struct drm_mode_get_connector *conn,
		     const uint32_t *encoders, char *err, size_t err_len)
{
	struct drm_mode_card_res res = { 0 };
	uint32_t *crtcs = NULL;
	uint32_t n_crtcs;
	int ret = -1;

	if (xioctl(d->fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0)
		return fail(err, err_len, "%s: cannot read KMS resources: %s",
			    d->card, strerror(errno));
	n_crtcs = res.count_crtcs;
	crtcs = calloc(n_crtcs ? n_crtcs : 1, sizeof(*crtcs));
	if (!crtcs)
		return fail(err, err_len, "out of memory");
	memset(&res, 0, sizeof(res));
	res.count_crtcs = n_crtcs;
	res.crtc_id_ptr = (uintptr_t)crtcs;
	if (xioctl(d->fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0 ||
	    res.count_crtcs > n_crtcs) {
		fail(err, err_len, "%s: cannot read its CRTCs", d->card);
		goto out;
	}

	for (int i = -1; i < (int)conn->count_encoders && !d->crtc_id; i++) {
		struct drm_mode_get_encoder enc = { 0 };

		enc.encoder_id = i < 0 ? conn->encoder_id : encoders[i];
		if (!enc.encoder_id || xioctl(d->fd, DRM_IOCTL_MODE_GETENCODER, &enc) < 0)
			continue;
		if (enc.crtc_id) {
			d->crtc_id = enc.crtc_id;
			break;
		}
		for (uint32_t c = 0; c < n_crtcs && c < 32; c++) {
			if (enc.possible_crtcs & (1u << c)) {
				d->crtc_id = crtcs[c];
				break;
			}
		}
	}
	if (!d->crtc_id)
		fail(err, err_len, "%s: no CRTC can drive the connector", d->card);
	else
		ret = 0;
out:
	free(crtcs);
	return ret;
}

int display_open(struct display *d, const char *connector, char *err, size_t err_len)
{
	struct drm_mode_modeinfo *modes = NULL, mode;
	struct drm_mode_get_connector conn = { 0 };
	struct drm_mode_create_dumb create = { 0 };
	struct drm_mode_fb_cmd2 fb = { 0 };
	struct drm_mode_map_dumb map = { 0 };
	struct drm_mode_crtc crtc = { 0 };
	uint32_t *encoders = NULL;
	uint32_t n_modes, n_encoders;
	char node[32];
	int ret = -1;

	memset(d, 0, sizeof(*d));
	d->fd = -1;

	if (!display_connector_valid(connector))
		return fail(err, err_len, "invalid connector name '%s'", connector);
	if (find_connector(d, connector, err, err_len) < 0)
		return -1;

	snprintf(node, sizeof(node), "/dev/dri/%s", d->card);
	d->fd = open(node, O_RDWR | O_CLOEXEC);
	if (d->fd < 0) {
		fail(err, err_len, "cannot open %s: %s", node, strerror(errno));
		goto out;
	}

	/* The connector's state and modes; the first call also probes it. */
	conn.connector_id = d->connector_id;
	if (xioctl(d->fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) < 0) {
		fail(err, err_len, "%s: cannot read connector %s: %s",
		     d->card, connector, strerror(errno));
		goto out;
	}
	if (conn.connection == CONNECTOR_DISCONNECTED || conn.count_modes == 0) {
		fail(err, err_len, "%s: connector %s is disconnected or has no modes",
		     d->card, connector);
		goto out;
	}
	n_modes = conn.count_modes;
	n_encoders = conn.count_encoders;
	modes = calloc(n_modes, sizeof(*modes));
	encoders = calloc(n_encoders ? n_encoders : 1, sizeof(*encoders));
	if (!modes || !encoders) {
		fail(err, err_len, "out of memory");
		goto out;
	}
	memset(&conn, 0, sizeof(conn));
	conn.connector_id = d->connector_id;
	conn.count_modes = n_modes;
	conn.modes_ptr = (uintptr_t)modes;
	conn.count_encoders = n_encoders;
	conn.encoders_ptr = (uintptr_t)encoders;
	if (xioctl(d->fd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) < 0 ||
	    conn.count_modes > n_modes || conn.count_encoders > n_encoders) {
		fail(err, err_len, "%s: connector %s changed while being read",
		     d->card, connector);
		goto out;
	}

	mode = modes[0];
	for (uint32_t i = 0; i < conn.count_modes; i++) {
		if (modes[i].type & DRM_MODE_TYPE_PREFERRED) {
			mode = modes[i];
			break;
		}
	}
	if (find_crtc(d, &conn, encoders, err, err_len) < 0)
		goto out;

	/* A buffer in XRGB8888, the format every KMS driver supports. */
	create.width = mode.hdisplay;
	create.height = mode.vdisplay;
	create.bpp = 32;
	if (xioctl(d->fd, DRM_IOCTL_MODE_CREATE_DUMB, &create) < 0) {
		fail(err, err_len, "%s: cannot create a %ux%u buffer: %s",
		     d->card, mode.hdisplay, mode.vdisplay, strerror(errno));
		goto out;
	}
	d->handle = create.handle;
	d->size = create.size;

	fb.width = create.width;
	fb.height = create.height;
	fb.pixel_format = DRM_FORMAT_XRGB8888;
	fb.handles[0] = create.handle;
	fb.pitches[0] = create.pitch;
	if (xioctl(d->fd, DRM_IOCTL_MODE_ADDFB2, &fb) < 0) {
		fail(err, err_len, "%s: cannot add the framebuffer: %s",
		     d->card, strerror(errno));
		goto out;
	}
	d->fb_id = fb.fb_id;

	map.handle = create.handle;
	if (xioctl(d->fd, DRM_IOCTL_MODE_MAP_DUMB, &map) < 0) {
		fail(err, err_len, "%s: cannot map the buffer: %s", d->card, strerror(errno));
		goto out;
	}
	d->map = mmap(NULL, d->size, PROT_READ | PROT_WRITE, MAP_SHARED, d->fd, map.offset);
	if (d->map == MAP_FAILED) {
		d->map = NULL;
		fail(err, err_len, "%s: cannot map the buffer: %s", d->card, strerror(errno));
		goto out;
	}
	memset(d->map, 0, d->size);
	d->canvas.pixels = d->map;
	d->canvas.width = create.width;
	d->canvas.height = create.height;
	d->canvas.stride = create.pitch / 4;

	crtc.crtc_id = d->crtc_id;
	crtc.fb_id = d->fb_id;
	crtc.set_connectors_ptr = (uintptr_t)&d->connector_id;
	crtc.count_connectors = 1;
	crtc.mode = mode;
	crtc.mode_valid = 1;
	if (xioctl(d->fd, DRM_IOCTL_MODE_SETCRTC, &crtc) < 0) {
		if (errno == EACCES)
			fail(err, err_len, "%s: another program controls the display "
			     "(DRM master)", d->card);
		else
			fail(err, err_len, "%s: cannot set mode %s on %s: %s",
			     d->card, mode.name, connector, strerror(errno));
		goto out;
	}
	ret = 0;
out:
	free(modes);
	free(encoders);
	if (ret < 0)
		display_close(d);
	return ret;
}

int display_flush(struct display *d)
{
	struct drm_mode_fb_dirty_cmd dirty = { .fb_id = d->fb_id };

	/* No clips: the whole framebuffer. */
	if (xioctl(d->fd, DRM_IOCTL_MODE_DIRTYFB, &dirty) < 0 && errno != ENOSYS)
		return -1;
	return 0;
}

void display_close(struct display *d)
{
	if (d->map)
		munmap(d->map, d->size);
	if (d->fd >= 0) {
		if (d->fb_id)
			xioctl(d->fd, DRM_IOCTL_MODE_RMFB, &d->fb_id);
		if (d->handle) {
			struct drm_mode_destroy_dumb destroy = { .handle = d->handle };

			xioctl(d->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
		}
		close(d->fd);
	}
	memset(d, 0, sizeof(*d));
	d->fd = -1;
}
