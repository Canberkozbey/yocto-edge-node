// SPDX-License-Identifier: MIT
/*
 * Analog readings through the kernel's IIO interface (sysfs).
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "sensors.h"

/*
 * The IIO device directory under the ADC's sysfs directory. Its number
 * (iio:deviceN) depends on probe order, so it is looked up on every read.
 */
static int find_iio_device(const char *adc_device, char *out, size_t len)
{
	char pattern[PATH_MAX + 16];
	glob_t found;
	int ret = -1;

	snprintf(pattern, sizeof(pattern), "%s/iio:device*", adc_device);
	if (glob(pattern, 0, NULL, &found) == 0 && found.gl_pathc == 1 &&
	    strlen(found.gl_pathv[0]) < len) {
		snprintf(out, len, "%s", found.gl_pathv[0]);
		ret = 0;
	}
	globfree(&found);
	return ret;
}

/* Reads in_voltage<index>_<attr> of an IIO device into buf, without the newline. */
static int read_iio_attr(const char *iio_dir, unsigned int index, const char *attr,
			 char *buf, size_t len)
{
	char path[PATH_MAX + 64];
	ssize_t n;
	int fd;

	snprintf(path, sizeof(path), "%s/in_voltage%u_%s", iio_dir, index, attr);
	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -1;
	/* For a raw value this read starts a conversion and waits for it. */
	n = read(fd, buf, len - 1);
	close(fd);
	if (n <= 0)
		return -1;
	buf[n] = '\0';
	buf[strcspn(buf, "\n")] = '\0';
	return 0;
}

static int read_millivolts(const char *iio_dir, unsigned int index,
			   long *raw, double *millivolts)
{
	char text[64];
	double scale;
	char *end;

	if (read_iio_attr(iio_dir, index, "raw", text, sizeof(text)) < 0)
		return -1;
	errno = 0;
	*raw = strtol(text, &end, 10);
	if (errno || end == text || *end != '\0')
		return -1;

	/* IIO voltage scales are millivolts per count. */
	if (read_iio_attr(iio_dir, index, "scale", text, sizeof(text)) < 0)
		return -1;
	errno = 0;
	scale = strtod(text, &end);
	if (errno || end == text || *end != '\0')
		return -1;

	*millivolts = (double)*raw * scale;
	return 0;
}

void read_sensors(const char *adc_device, const struct channel *channels,
		  unsigned int count, struct reading *out)
{
	char iio_dir[PATH_MAX];
	int have_device;

	have_device = adc_device[0] &&
		find_iio_device(adc_device, iio_dir, sizeof(iio_dir)) == 0;

	for (unsigned int i = 0; i < count; i++) {
		struct reading *r = &out[i];

		r->channel = &channels[i];
		r->raw = 0;
		r->millivolts = 0;
		if (!have_device)
			r->status = READING_NO_DEVICE;
		else if (read_millivolts(iio_dir, channels[i].index,
					 &r->raw, &r->millivolts) < 0)
			r->status = READING_FAILED;
		else
			r->status = READING_OK;
	}
}
