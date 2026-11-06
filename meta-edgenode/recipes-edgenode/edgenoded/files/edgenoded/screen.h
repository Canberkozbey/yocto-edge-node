/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_SCREEN_H
#define EDGENODED_SCREEN_H

#include "draw.h"
#include "sensors.h"

/*
 * What the screen shows, as text: a title (the host name) and one line per
 * analog input. Built first and drawn second, so an unchanged screen can be
 * recognised (memcmp) and not sent to the panel again.
 */
struct screen_item {
	char name[CHANNEL_NAME_MAX];
	char value[24];		/* millivolts, or the reason there is none */
	enum reading_status status;
};

struct screen {
	char title[65];
	unsigned int count;
	struct screen_item items[MAX_CHANNELS];
};

void screen_build(struct screen *s, const char *title,
		  const struct reading *readings, unsigned int count);

void screen_draw(const struct canvas *c, const struct screen *s);

#endif
