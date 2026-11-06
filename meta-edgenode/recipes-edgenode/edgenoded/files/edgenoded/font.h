/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_FONT_H
#define EDGENODED_FONT_H

#include <stdint.h>

/*
 * A fixed-width bitmap font: `count` glyphs from character `first`, each
 * `height` rows of uint16_t with the leftmost pixel in bit 15. The fonts
 * themselves are generated from BDF files at build time (bdf2c.py).
 */
struct font {
	unsigned int width;
	unsigned int height;
	unsigned int ascent;	/* rows above the baseline */
	unsigned char first;
	unsigned int count;
	const uint16_t *rows;
};

/* Spleen (BSD-2-Clause), https://github.com/fcambus/spleen */
extern const struct font font_small;	/* 12x24 */
extern const struct font font_large;	/* 16x32 */

#endif
