/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_DISPLAY_H
#define EDGENODED_DISPLAY_H

#include <stddef.h>
#include <stdint.h>

#include "draw.h"

/*
 * One DRM/KMS output: a connector, the CRTC driving it and a dumb buffer
 * the CPU draws into, through the kernel's KMS ioctls (no libdrm).
 */
struct display {
	int fd;
	char card[16];			/* e.g. "card1", for messages */
	uint32_t connector_id;
	uint32_t crtc_id;
	uint32_t fb_id;
	uint32_t handle;		/* the dumb buffer's GEM handle */
	uint64_t size;
	void *map;
	struct canvas canvas;		/* the buffer, as the CPU sees it */
};

/* Valid connector names, as /sys/class/drm spells them: "SPI-1", "HDMI-A-1". */
int display_connector_valid(const char *connector);

/*
 * Finds the connector by name (/sys/class/drm/card*-<connector>), sets its
 * preferred mode with a black buffer and returns 0. On failure returns -1
 * with the reason in err; d is then closed.
 */
int display_open(struct display *d, const char *connector, char *err, size_t err_len);

/* Shows what was drawn on d->canvas; -1 with errno set on failure. */
int display_flush(struct display *d);

/* Frees the buffer; the kernel gives the output back (e.g. to the console). */
void display_close(struct display *d);

#endif
