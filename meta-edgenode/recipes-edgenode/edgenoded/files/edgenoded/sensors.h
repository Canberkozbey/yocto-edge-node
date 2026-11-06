/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_SENSORS_H
#define EDGENODED_SENSORS_H

#define MAX_CHANNELS     8
#define CHANNEL_NAME_MAX 32
#define MAX_IIO_CHANNEL  15

/* One analog input: a name for the API and the ADC's IIO channel index. */
struct channel {
	char name[CHANNEL_NAME_MAX];
	unsigned int index;
};

enum reading_status {
	READING_OK,
	READING_NO_DEVICE,	/* the ADC's IIO device is not there */
	READING_FAILED,		/* the input could not be read */
};

struct reading {
	const struct channel *channel;
	enum reading_status status;
	long raw;
	double millivolts;
};

/*
 * Reads each channel of the IIO device under adc_device (its sysfs
 * directory) into out[i]. adc_device may be "" when there is no ADC.
 */
void read_sensors(const char *adc_device, const struct channel *channels,
		  unsigned int count, struct reading *out);

#endif
