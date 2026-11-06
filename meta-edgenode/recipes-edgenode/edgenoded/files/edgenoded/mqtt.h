/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_MQTT_H
#define EDGENODED_MQTT_H

#include <stddef.h>

/*
 * Publishing to an MQTT broker (MQTT 3.1.1, plain TCP) through libmosquitto,
 * whose network thread connects, keeps the connection alive and reconnects.
 * Topics, under edgenode/<hostname>/:
 *   status   "online" (retained) once connected; "offline" (retained) on
 *            mqtt_stop(), and as the last will when the connection drops
 *   sensors  the readings, as served at /sensors (retained)
 */
struct mqtt;

/* Starts connecting in the background; NULL with the reason in err. */
struct mqtt *mqtt_start(const char *broker, unsigned short port,
			char *err, size_t err_len);

/* Publishes payload to <prefix>/sensors; a no-op while disconnected. */
void mqtt_publish_sensors(struct mqtt *m, const char *payload, size_t len);

/* Publishes "offline", disconnects and frees m. */
void mqtt_stop(struct mqtt *m);

#endif
