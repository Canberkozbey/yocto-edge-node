// SPDX-License-Identifier: MIT
/*
 * Rectangles and bitmap text on an XRGB8888 canvas.
 */

#include <string.h>

#include "draw.h"

void draw_rect(const struct canvas *c, int x, int y, int w, int h, uint32_t color)
{
	int x0 = x < 0 ? 0 : x;
	int y0 = y < 0 ? 0 : y;
	int x1 = x + w > (int)c->width ? (int)c->width : x + w;
	int y1 = y + h > (int)c->height ? (int)c->height : y + h;

	for (int row = y0; row < y1; row++) {
		uint32_t *p = c->pixels + (size_t)row * c->stride;

		for (int col = x0; col < x1; col++)
			p[col] = color;
	}
}

static const uint16_t *glyph(const struct font *f, unsigned char ch)
{
	if (ch < f->first || ch >= f->first + f->count)
		ch = '?';
	return f->rows + (size_t)(ch - f->first) * f->height;
}

void draw_text(const struct canvas *c, const struct font *f, int x, int y,
	       uint32_t color, const char *text)
{
	for (; *text; text++, x += (int)f->width) {
		const uint16_t *rows = glyph(f, (unsigned char)*text);

		if (x >= (int)c->width)
			break;
		for (unsigned int gy = 0; gy < f->height; gy++) {
			int py = y + (int)gy;
			uint32_t *p;

			if (py < 0 || py >= (int)c->height)
				continue;
			p = c->pixels + (size_t)py * c->stride;
			for (unsigned int gx = 0; gx < f->width; gx++) {
				int px = x + (int)gx;

				if (px >= 0 && px < (int)c->width &&
				    (rows[gy] >> (15 - gx) & 1))
					p[px] = color;
			}
		}
	}
}

int text_width(const struct font *f, const char *text)
{
	return (int)(strlen(text) * f->width);
}
