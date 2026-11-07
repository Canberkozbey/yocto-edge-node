/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_MQTT_H
#define EDGENODED_MQTT_H

#include <stddef.h>

/*
 * Publishing to an MQTT broker (MQTT 3.1.1, over TCP or TLS) through
 * libmosquitto, from a network thread that connects, keeps the connection
 * alive and reconnects. Topics, under edgenode/<hostname>/:
 *   status   "online" (retained) once connected; "offline" (retained) on
 *            mqtt_stop(), and as the last will when the connection drops
 *   sensors  the readings, as served at /sensors (retained)
 */
struct mqtt;

/*
 * TLS: the broker's certificate must chain to the CA certificate in ca and
 * name the broker as it is configured (host name or address). With cert and
 * key, the node also proves who it is (mutual TLS); both or neither.
 */
struct mqtt_tls {
	const char *ca;
	const char *cert;	/* NULL: no client certificate */
	const char *key;
};

/* Starts connecting in the background, over TLS when tls is not NULL;
 * NULL with the reason in err. */
struct mqtt *mqtt_start(const char *broker, unsigned short port,
			const struct mqtt_tls *tls, char *err, size_t err_len);

/* Publishes payload to <prefix>/sensors; a no-op while disconnected. */
void mqtt_publish_sensors(struct mqtt *m, const char *payload, size_t len);

/* Publishes "offline", disconnects and frees m. */
void mqtt_stop(struct mqtt *m);

#endif
