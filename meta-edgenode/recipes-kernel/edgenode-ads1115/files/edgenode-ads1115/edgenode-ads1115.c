// SPDX-License-Identifier: GPL-2.0-only
/*
 * TI ADS1115 16-bit ADC: four single-ended inputs, read one conversion at a
 * time through IIO.
 *
 * A deliberately small driver for the edge node. Every read of
 * in_voltageN_raw starts a single-shot conversion of AINN against GND,
 * waits for it and returns the signed result; in_voltageN_scale gives
 * millivolts per count for the full-scale range set in the device tree.
 * Between reads the converter powers itself down. The mainline ti-ads1015
 * driver covers much more (differential inputs, buffered capture,
 * threshold events, runtime PM); the edge node's tests read the same
 * values through either driver (docs/adr/0004).
 *
 * Datasheet: https://www.ti.com/lit/ds/symlink/ads1115.pdf
 */

#include <linux/bitfield.h>
#include <linux/bits.h>
#include <linux/cleanup.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/iio/iio.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/property.h>

#define ADS1115_REG_CONVERSION		0x00
#define ADS1115_REG_CONFIG		0x01

/* Config register fields (datasheet, section 8.6.3) */
#define ADS1115_CFG_OS			BIT(15)	/* write 1: start; reads 1: idle */
#define ADS1115_CFG_MUX			GENMASK(14, 12)
#define ADS1115_CFG_PGA			GENMASK(11, 9)
#define ADS1115_CFG_MODE_SINGLE		BIT(8)
#define ADS1115_CFG_DR			GENMASK(7, 5)
#define ADS1115_CFG_COMP_QUE		GENMASK(1, 0)

#define ADS1115_MUX_AIN_GND(ch)		(0x4 + (ch))	/* AINx against GND */
#define ADS1115_DR_128SPS		0x4		/* the power-on default */
#define ADS1115_COMP_QUE_OFF		0x3

/* One conversion at 128 samples/s, plus 10 % for the internal oscillator. */
#define ADS1115_CONVERSION_US		(1000000 / 128 * 11 / 10)
#define ADS1115_CONVERSION_POLLS	3

#define ADS1115_NUM_CHANNELS		4
#define ADS1115_DEFAULT_FULL_SCALE_MV	2048		/* the power-on default */

/* Full-scale range in millivolts, indexed by PGA code (codes 6 and 7 repeat 256) */
static const unsigned int ads1115_full_scale_mv[] = { 6144, 4096, 2048, 1024, 512, 256 };

struct ads1115 {
	struct i2c_client *client;
	/* One conversion at a time: the config register is shared. */
	struct mutex lock;
	u8 pga;
};

#define ADS1115_CHANNEL(index) {				\
	.type = IIO_VOLTAGE,					\
	.indexed = 1,						\
	.channel = (index),					\
	.info_mask_separate = BIT(IIO_CHAN_INFO_RAW) |		\
			      BIT(IIO_CHAN_INFO_SCALE),		\
}

static const struct iio_chan_spec ads1115_channels[ADS1115_NUM_CHANNELS] = {
	ADS1115_CHANNEL(0),
	ADS1115_CHANNEL(1),
	ADS1115_CHANNEL(2),
	ADS1115_CHANNEL(3),
};

static int ads1115_convert(struct ads1115 *adc, unsigned int channel, int *val)
{
	struct i2c_client *client = adc->client;
	u16 config;
	int ret, poll;

	config = ADS1115_CFG_OS |
		 FIELD_PREP(ADS1115_CFG_MUX, ADS1115_MUX_AIN_GND(channel)) |
		 FIELD_PREP(ADS1115_CFG_PGA, adc->pga) |
		 ADS1115_CFG_MODE_SINGLE |
		 FIELD_PREP(ADS1115_CFG_DR, ADS1115_DR_128SPS) |
		 FIELD_PREP(ADS1115_CFG_COMP_QUE, ADS1115_COMP_QUE_OFF);

	guard(mutex)(&adc->lock);

	ret = i2c_smbus_write_word_swapped(client, ADS1115_REG_CONFIG, config);
	if (ret)
		return ret;

	for (poll = 0; poll < ADS1115_CONVERSION_POLLS; poll++) {
		fsleep(ADS1115_CONVERSION_US);
		ret = i2c_smbus_read_word_swapped(client, ADS1115_REG_CONFIG);
		if (ret < 0)
			return ret;
		if (ret & ADS1115_CFG_OS)
			break;
	}
	if (!(ret & ADS1115_CFG_OS))
		return -ETIMEDOUT;

	ret = i2c_smbus_read_word_swapped(client, ADS1115_REG_CONVERSION);
	if (ret < 0)
		return ret;

	*val = sign_extend32(ret, 15);
	return 0;
}

static int ads1115_read_raw(struct iio_dev *indio_dev,
			    struct iio_chan_spec const *chan,
			    int *val, int *val2, long mask)
{
	struct ads1115 *adc = iio_priv(indio_dev);
	int ret;

	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		ret = ads1115_convert(adc, chan->channel, val);
		return ret ? ret : IIO_VAL_INT;
	case IIO_CHAN_INFO_SCALE:
		/* millivolts per count: the full-scale range over 2^15 */
		*val = ads1115_full_scale_mv[adc->pga];
		*val2 = 15;
		return IIO_VAL_FRACTIONAL_LOG2;
	default:
		return -EINVAL;
	}
}

static const struct iio_info ads1115_info = {
	.read_raw = ads1115_read_raw,
};

static int ads1115_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	u32 full_scale_mv = ADS1115_DEFAULT_FULL_SCALE_MV;
	struct iio_dev *indio_dev;
	struct ads1115 *adc;
	unsigned int pga;
	int ret;

	indio_dev = devm_iio_device_alloc(dev, sizeof(*adc));
	if (!indio_dev)
		return -ENOMEM;

	adc = iio_priv(indio_dev);
	adc->client = client;
	ret = devm_mutex_init(dev, &adc->lock);
	if (ret)
		return ret;

	device_property_read_u32(dev, "edgenode,full-scale-mv", &full_scale_mv);
	for (pga = 0; pga < ARRAY_SIZE(ads1115_full_scale_mv); pga++)
		if (ads1115_full_scale_mv[pga] == full_scale_mv)
			break;
	if (pga == ARRAY_SIZE(ads1115_full_scale_mv))
		return dev_err_probe(dev, -EINVAL,
				     "unsupported full scale: %u mV\n", full_scale_mv);
	adc->pga = pga;

	/* Fail at probe, not at the first read, when nothing answers. */
	ret = i2c_smbus_read_word_swapped(client, ADS1115_REG_CONFIG);
	if (ret < 0)
		return dev_err_probe(dev, ret, "no response at 0x%02x\n", client->addr);

	indio_dev->name = "ads1115";
	indio_dev->info = &ads1115_info;
	indio_dev->modes = INDIO_DIRECT_MODE;
	indio_dev->channels = ads1115_channels;
	indio_dev->num_channels = ARRAY_SIZE(ads1115_channels);

	return devm_iio_device_register(dev, indio_dev);
}

static const struct of_device_id ads1115_of_match[] = {
	{ .compatible = "edgenode,ads1115" },
	{ }
};
MODULE_DEVICE_TABLE(of, ads1115_of_match);

static const struct i2c_device_id ads1115_id[] = {
	{ "edgenode-ads1115" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, ads1115_id);

static struct i2c_driver ads1115_driver = {
	.driver = {
		.name = "edgenode-ads1115",
		.of_match_table = ads1115_of_match,
	},
	.probe = ads1115_probe,
	.id_table = ads1115_id,
};
module_i2c_driver(ads1115_driver);

MODULE_AUTHOR("Canberk Özbey");
MODULE_DESCRIPTION("TI ADS1115 ADC, single-shot reads through IIO");
MODULE_LICENSE("GPL");
