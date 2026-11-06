// SPDX-License-Identifier: MIT
/*
 * The screen's layout: a title bar with the host name, then each analog
 * input's name with its reading below, right-aligned. Laid out for the
 * 240x240 panel; a larger output shows the same from its top-left corner.
 */

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "font.h"
#include "screen.h"

#define MARGIN   6
#define GAP      4
#define TITLE_H  ((int)font_small.height + 6)

#define COLOR_BACKGROUND RGB(0x00, 0x00, 0x00)
#define COLOR_TITLE_BAR  RGB(0x2a, 0x9d, 0x8f)
#define COLOR_TITLE      RGB(0x00, 0x00, 0x00)
#define COLOR_LABEL      RGB(0xa0, 0xa0, 0xa0)
#define COLOR_VALUE      RGB(0xff, 0xff, 0xff)
#define COLOR_ERROR      RGB(0xff, 0x55, 0x55)

#define UNIT " mV"

void screen_build(struct screen *s, const char *title,
		  const struct reading *readings, unsigned int count)
{
	/* Zeroed whole, padding included, so two screens compare with memcmp. */
	memset(s, 0, sizeof(*s));
	snprintf(s->title, sizeof(s->title), "%s", title);
	s->count = count < MAX_CHANNELS ? count : MAX_CHANNELS;

	for (unsigned int i = 0; i < s->count; i++) {
		const struct reading *r = &readings[i];
		struct screen_item *item = &s->items[i];
		/* No "-0" for readings just below zero. */
		double mv = fabs(r->millivolts) < 0.5 ? 0.0 : r->millivolts;

		snprintf(item->name, sizeof(item->name), "%s", r->channel->name);
		item->status = r->status;
		switch (r->status) {
		case READING_OK:
			snprintf(item->value, sizeof(item->value), "%.0f", mv);
			break;
		case READING_NO_DEVICE:
			snprintf(item->value, sizeof(item->value), "no ADC");
			break;
		case READING_FAILED:
			snprintf(item->value, sizeof(item->value), "read failed");
			break;
		}
	}
}

/* Draws at most as many characters as fit between x and the right margin. */
static void draw_fitted(const struct canvas *c, const struct font *f, int x, int y,
			uint32_t color, const char *text)
{
	char fitted[128];
	int room = ((int)c->width - MARGIN - x) / (int)f->width;

	if (room <= 0)
		return;
	if (room >= (int)sizeof(fitted))
		room = (int)sizeof(fitted) - 1;
	snprintf(fitted, (size_t)room + 1, "%s", text);
	draw_text(c, f, x, y, color, fitted);
}

void screen_draw(const struct canvas *c, const struct screen *s)
{
	const int right = (int)c->width - MARGIN;
	/* Offset that puts small text on the large text's baseline. */
	const int baseline = (int)font_large.ascent - (int)font_small.ascent;
	int y;

	draw_rect(c, 0, 0, (int)c->width, (int)c->height, COLOR_BACKGROUND);
	draw_rect(c, 0, 0, (int)c->width, TITLE_H, COLOR_TITLE_BAR);
	draw_fitted(c, &font_small, MARGIN, 3, COLOR_TITLE, s->title);

	y = TITLE_H + GAP;
	if (s->count == 0)
		draw_fitted(c, &font_small, MARGIN, y, COLOR_LABEL, "no inputs");

	for (unsigned int i = 0; i < s->count; i++) {
		const struct screen_item *item = &s->items[i];
		int value_y = y + (int)font_small.height;

		if (value_y + (int)font_large.height > (int)c->height)
			break;	/* no room for more */
		draw_fitted(c, &font_small, MARGIN, y, COLOR_LABEL, item->name);

		if (item->status == READING_OK) {
			int unit_x = right - text_width(&font_small, UNIT);

			draw_text(c, &font_large, unit_x - text_width(&font_large, item->value),
				  value_y, COLOR_VALUE, item->value);
			draw_text(c, &font_small, unit_x, value_y + baseline, COLOR_LABEL, UNIT);
		} else {
			draw_text(c, &font_small, right - text_width(&font_small, item->value),
				  value_y + baseline, COLOR_ERROR, item->value);
		}
		y = value_y + (int)font_large.height + GAP;
	}
}
