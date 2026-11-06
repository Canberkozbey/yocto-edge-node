/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_DRAW_H
#define EDGENODED_DRAW_H

#include <stdint.h>

#include "font.h"

/* A 32-bit XRGB8888 pixel buffer; stride is in pixels. */
struct canvas {
	uint32_t *pixels;
	unsigned int width;
	unsigned int height;
	unsigned int stride;
};

#define RGB(r, g, b) ((uint32_t)(r) << 16 | (uint32_t)(g) << 8 | (uint32_t)(b))

/* Fills a rectangle, clipped to the canvas. */
void draw_rect(const struct canvas *c, int x, int y, int w, int h, uint32_t color);

/*
 * Draws text with its top-left corner at (x, y), clipped to the canvas.
 * Characters the font lacks are drawn as '?'.
 */
void draw_text(const struct canvas *c, const struct font *f, int x, int y,
	       uint32_t color, const char *text);

/* Width of text in pixels, drawn in font f. */
int text_width(const struct font *f, const char *text);

#endif
