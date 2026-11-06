// SPDX-License-Identifier: MIT
/*
 * MQTT publishing through libmosquitto.
 *
 * A network thread of our own runs the connection: it connects, lets
 * mosquitto_loop() handle traffic and keep-alive pings, and reconnects with
 * a growing delay when the broker cannot be reached. libmosquitto's own
 * thread (mosquitto_loop_start) would stop for good after a name resolution
 * failure, as at boot before the network is up. The main thread only
 * publishes, which libmosquitto allows from any thread once told about the
 * threads (mosquitto_threaded_set). Callbacks run on the network thread.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <mosquitto.h>

#include "log.h"
#include "mqtt.h"

#define KEEPALIVE_S      30
#define RETRY_MIN_MS     1000
#define RETRY_MAX_MS     30000
#define LOOP_TIMEOUT_MS  500	/* how long the thread may take to see a stop */
#define STOP_TIMEOUT_MS  2000

struct mqtt {
	struct mosquitto *mosq;
	pthread_t thread;
	char broker[256];
	unsigned short port;
	char prefix[96];		/* edgenode/<hostname> */
	char status_topic[112];
	char sensors_topic[112];

	atomic_bool connected;
	atomic_bool stopping;
	atomic_llong stop_deadline_ms;
	atomic_int last_mid;		/* of the last publish the broker took */

	/* network thread only */
	long long retry_ms;
	bool failure_logged;
};

static long long now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void sleep_ms(long ms)
{
	struct timespec ts = { .tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000 };

	while (nanosleep(&ts, &ts) < 0 && errno == EINTR)
		;
}

static void on_connect(struct mosquitto *mosq, void *obj, int rc)
{
	struct mqtt *m = obj;

	if (rc != 0) {
		log_msg(PRI_WARN, "MQTT broker %s:%u refused the connection: %s",
			m->broker, m->port, mosquitto_connack_string(rc));
		return;
	}
	atomic_store(&m->connected, true);
	m->retry_ms = RETRY_MIN_MS;
	m->failure_logged = false;
	log_msg(PRI_INFO, "Connected to MQTT broker %s:%u, publishing under %s/",
		m->broker, m->port, m->prefix);
	mosquitto_publish(mosq, NULL, m->status_topic, 6, "online", 1, true);
}

static void on_disconnect(struct mosquitto *mosq, void *obj, int rc)
{
	struct mqtt *m = obj;

	(void)mosq;
	if (atomic_exchange(&m->connected, false) && rc != 0) {
		log_msg(PRI_WARN, "Lost MQTT broker %s:%u (%s), reconnecting",
			m->broker, m->port, mosquitto_strerror(rc));
		m->failure_logged = true;
	}
}

static void on_publish(struct mosquitto *mosq, void *obj, int mid)
{
	struct mqtt *m = obj;

	(void)mosq;
	atomic_store(&m->last_mid, mid);
}

static void connection_failed(struct mqtt *m, int rc)
{
	if (!m->failure_logged)
		log_msg(PRI_WARN, "Cannot reach MQTT broker %s:%u (%s), retrying",
			m->broker, m->port,
			rc == MOSQ_ERR_ERRNO ? strerror(errno) : mosquitto_strerror(rc));
	m->failure_logged = true;
}

static void *network_thread(void *arg)
{
	struct mqtt *m = arg;
	long long next_attempt = 0;
	bool first = true;
	bool open = false;	/* a socket is connecting or connected */

	for (;;) {
		int rc;

		if (atomic_load(&m->stopping) &&
		    (!open || !atomic_load(&m->connected) ||
		     now_ms() >= atomic_load(&m->stop_deadline_ms)))
			break;

		if (!open) {
			if (now_ms() < next_attempt) {
				sleep_ms(100);
				continue;
			}
			/* The first call stores broker and port; the name is
			 * resolved on every attempt. */
			rc = first ? mosquitto_connect_async(m->mosq, m->broker, m->port, KEEPALIVE_S)
				   : mosquitto_reconnect_async(m->mosq);
			first = false;
			if (rc != MOSQ_ERR_SUCCESS) {
				connection_failed(m, rc);
				next_attempt = now_ms() + m->retry_ms;
				m->retry_ms = m->retry_ms * 2 > RETRY_MAX_MS ? RETRY_MAX_MS : m->retry_ms * 2;
				continue;
			}
			open = true;
		}

		rc = mosquitto_loop(m->mosq, LOOP_TIMEOUT_MS, 1);
		if (rc != MOSQ_ERR_SUCCESS) {
			/* Connection failed or closed; the socket is gone. */
			if (!atomic_load(&m->stopping))
				connection_failed(m, rc);
			open = false;
			next_attempt = now_ms() + m->retry_ms;
			m->retry_ms = m->retry_ms * 2 > RETRY_MAX_MS ? RETRY_MAX_MS : m->retry_ms * 2;
		}
	}
	return NULL;
}

struct mqtt *mqtt_start(const char *broker, unsigned short port,
			char *err, size_t err_len)
{
	char hostname[65] = "";
	char id[96];
	struct mqtt *m;
	int rc;

	m = calloc(1, sizeof(*m));
	if (!m) {
		snprintf(err, err_len, "out of memory");
		return NULL;
	}
	if (gethostname(hostname, sizeof(hostname) - 1) < 0 || !hostname[0])
		snprintf(hostname, sizeof(hostname), "edgenode");
	snprintf(m->broker, sizeof(m->broker), "%s", broker);
	m->port = port;
	snprintf(m->prefix, sizeof(m->prefix), "edgenode/%s", hostname);
	snprintf(m->status_topic, sizeof(m->status_topic), "%s/status", m->prefix);
	snprintf(m->sensors_topic, sizeof(m->sensors_topic), "%s/sensors", m->prefix);
	atomic_init(&m->connected, false);
	atomic_init(&m->stopping, false);
	atomic_init(&m->stop_deadline_ms, 0);
	atomic_init(&m->last_mid, 0);
	m->retry_ms = RETRY_MIN_MS;

	mosquitto_lib_init();
	snprintf(id, sizeof(id), "edgenoded-%s-%ld", hostname, (long)getpid());
	m->mosq = mosquitto_new(id, true, m);
	if (!m->mosq) {
		snprintf(err, err_len, "cannot create the MQTT client: %s", strerror(errno));
		goto fail;
	}
	mosquitto_threaded_set(m->mosq, true);
	mosquitto_connect_callback_set(m->mosq, on_connect);
	mosquitto_disconnect_callback_set(m->mosq, on_disconnect);
	mosquitto_publish_callback_set(m->mosq, on_publish);
	rc = mosquitto_will_set(m->mosq, m->status_topic, 7, "offline", 1, true);
	if (rc != MOSQ_ERR_SUCCESS) {
		snprintf(err, err_len, "cannot set the MQTT last will: %s", mosquitto_strerror(rc));
		goto fail;
	}
	rc = pthread_create(&m->thread, NULL, network_thread, m);
	if (rc != 0) {
		snprintf(err, err_len, "cannot start the MQTT thread: %s", strerror(rc));
		goto fail;
	}
	return m;

fail:
	if (m->mosq)
		mosquitto_destroy(m->mosq);
	mosquitto_lib_cleanup();
	free(m);
	return NULL;
}

void mqtt_publish_sensors(struct mqtt *m, const char *payload, size_t len)
{
	if (!atomic_load(&m->connected))
		return;
	/* QoS 0: a newer reading follows soon; retained for late subscribers. */
	mosquitto_publish(m->mosq, NULL, m->sensors_topic, (int)len, payload, 0, true);
}

void mqtt_stop(struct mqtt *m)
{
	long long deadline = now_ms() + STOP_TIMEOUT_MS;
	int mid = 0;

	/* "offline" first: a clean disconnect makes the broker drop the will. */
	if (atomic_load(&m->connected) &&
	    mosquitto_publish(m->mosq, &mid, m->status_topic, 7, "offline", 1, true) ==
	    MOSQ_ERR_SUCCESS) {
		while (atomic_load(&m->last_mid) != mid && atomic_load(&m->connected) &&
		       now_ms() < deadline)
			sleep_ms(10);
	}
	atomic_store(&m->stop_deadline_ms, now_ms() + STOP_TIMEOUT_MS);
	atomic_store(&m->stopping, true);
	mosquitto_disconnect(m->mosq);
	pthread_join(m->thread, NULL);

	mosquitto_destroy(m->mosq);
	mosquitto_lib_cleanup();
	free(m);
}
