// SPDX-License-Identifier: MIT
/*
 * edgenoded - edge node daemon
 *
 * Serves the node's health and analog sensor readings over HTTP, shows the
 * readings on a display, and reports its state to systemd: readiness,
 * watchdog keep-alives and shutdown. The readings come from an ADC through
 * the kernel's IIO interface (sensors.c); the display is a DRM/KMS output
 * (display.c, screen.c). A later stage adds MQTT publishing.
 */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <getopt.h>
#include <limits.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "display.h"
#include "screen.h"
#include "sensors.h"

#ifndef EDGENODED_VERSION
#define EDGENODED_VERSION "0.0.0-dev"
#endif

#define DEFAULT_CONFIG   "/etc/edgenoded.conf"
#define DEFAULT_BIND     "0.0.0.0"
#define DEFAULT_PORT     8080
#define REQUEST_MAX      4096
#define CLIENT_TIMEOUT_S 2
#define SCREEN_PERIOD_MS 1000

/* journald reads a leading <N> as the syslog priority of the line. */
#define PRI_ERR  "<3>"
#define PRI_WARN "<4>"
#define PRI_INFO "<6>"

struct config {
	char bind[INET_ADDRSTRLEN];
	unsigned short port;
	/* sysfs directory of the ADC device, holding its iio:deviceN; "" if none */
	char adc_device[PATH_MAX];
	struct channel channels[MAX_CHANNELS];
	unsigned int num_channels;
	/* DRM connector to show the readings on ("SPI-1"); "" for none */
	char display_connector[32];
};

static volatile sig_atomic_t stop_requested;

__attribute__((format(printf, 2, 3)))
static void log_msg(const char *priority, const char *fmt, ...)
{
	va_list ap;

	fputs(priority, stderr);
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fputc('\n', stderr);
}

static long long now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/*
 * Minimal sd_notify(3): one datagram to $NOTIFY_SOCKET. The protocol is
 * small and documented as stable, so this avoids a libsystemd dependency
 * and the daemon still runs outside systemd, where NOTIFY_SOCKET is unset.
 */
static void notify(const char *state)
{
	const char *path = getenv("NOTIFY_SOCKET");
	struct sockaddr_un sa = { .sun_family = AF_UNIX };
	size_t len;
	int fd;

	if (!path || (path[0] != '/' && path[0] != '@'))
		return;
	len = strlen(path);
	if (len >= sizeof(sa.sun_path))
		return;
	memcpy(sa.sun_path, path, len);
	if (sa.sun_path[0] == '@')
		sa.sun_path[0] = '\0';	/* abstract socket namespace */

	fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return;
	if (sendto(fd, state, strlen(state), MSG_NOSIGNAL,
		   (struct sockaddr *)&sa,
		   (socklen_t)(offsetof(struct sockaddr_un, sun_path) + len)) < 0)
		log_msg(PRI_WARN, "sd_notify failed: %s", strerror(errno));
	close(fd);
}

/*
 * Returns the watchdog keep-alive interval in milliseconds, or -1 when
 * systemd does not supervise this process. Pings go out at half the
 * timeout, as sd_watchdog_enabled(3) recommends.
 */
static int watchdog_interval_ms(void)
{
	const char *usec = getenv("WATCHDOG_USEC");
	const char *pid = getenv("WATCHDOG_PID");
	unsigned long long value;
	char *end;

	if (!usec)
		return -1;
	if (pid && strtol(pid, NULL, 10) != (long)getpid())
		return -1;

	errno = 0;
	value = strtoull(usec, &end, 10);
	if (errno || *end != '\0' || value == 0)
		return -1;

	value /= 2000;	/* microseconds to milliseconds, halved */
	if (value == 0)
		value = 1;
	return value > INT_MAX ? INT_MAX : (int)value;
}

static char *trim(char *s)
{
	char *end;

	while (*s == ' ' || *s == '\t')
		s++;
	end = s + strlen(s);
	while (end > s && (end[-1] == ' ' || end[-1] == '\t'))
		*--end = '\0';
	return s;
}

static int parse_port(const char *text, unsigned short *port)
{
	unsigned long value;
	char *end;

	errno = 0;
	value = strtoul(text, &end, 10);
	if (errno || end == text || *end != '\0' || value == 0 || value > 65535)
		return -1;
	*port = (unsigned short)value;
	return 0;
}

/*
 * "channel.<name> = <index>": names become JSON keys, so they are limited
 * to [a-z0-9_-]; indexes are the ADC's IIO channels.
 */
static int add_channel(struct config *cfg, const char *name, const char *value)
{
	unsigned long index;
	size_t len = strlen(name);
	char *end;

	if (len == 0 || len >= CHANNEL_NAME_MAX ||
	    strspn(name, "abcdefghijklmnopqrstuvwxyz0123456789_-") != len)
		return -1;
	errno = 0;
	index = strtoul(value, &end, 10);
	if (errno || end == value || *end != '\0' || index > MAX_IIO_CHANNEL)
		return -1;
	for (unsigned int i = 0; i < cfg->num_channels; i++)
		if (strcmp(cfg->channels[i].name, name) == 0)
			return -1;
	if (cfg->num_channels == MAX_CHANNELS)
		return -1;

	snprintf(cfg->channels[cfg->num_channels].name, CHANNEL_NAME_MAX, "%s", name);
	cfg->channels[cfg->num_channels].index = (unsigned int)index;
	cfg->num_channels++;
	return 0;
}

/*
 * Reads "key = value" lines; '#' starts a comment. A missing file is fine
 * unless it was named explicitly. Unknown keys are reported, not fatal, so
 * a newer config file still works with an older daemon.
 */
static int load_config(const char *path, bool required, struct config *cfg)
{
	char line[256];
	unsigned int lineno = 0;
	int ret = 0;
	FILE *f;

	f = fopen(path, "re");
	if (!f) {
		if (errno == ENOENT && !required) {
			log_msg(PRI_INFO, "No config at %s, using defaults", path);
			return 0;
		}
		log_msg(PRI_ERR, "Cannot open %s: %s", path, strerror(errno));
		return -1;
	}

	while (fgets(line, sizeof(line), f)) {
		char *key, *value, *eq;

		lineno++;
		if (!strchr(line, '\n') && !feof(f)) {
			log_msg(PRI_ERR, "%s:%u: line too long", path, lineno);
			ret = -1;
			break;
		}
		line[strcspn(line, "#\r\n")] = '\0';
		key = trim(line);
		if (*key == '\0')
			continue;

		eq = strchr(key, '=');
		if (!eq) {
			log_msg(PRI_ERR, "%s:%u: expected key = value", path, lineno);
			ret = -1;
			break;
		}
		*eq = '\0';
		key = trim(key);
		value = trim(eq + 1);

		if (strcmp(key, "port") == 0) {
			if (parse_port(value, &cfg->port) < 0) {
				log_msg(PRI_ERR, "%s:%u: invalid port '%s'",
					path, lineno, value);
				ret = -1;
				break;
			}
		} else if (strcmp(key, "bind") == 0) {
			struct in_addr addr;

			if (inet_pton(AF_INET, value, &addr) != 1) {
				log_msg(PRI_ERR, "%s:%u: invalid IPv4 address '%s'",
					path, lineno, value);
				ret = -1;
				break;
			}
			snprintf(cfg->bind, sizeof(cfg->bind), "%s", value);
		} else if (strcmp(key, "adc_device") == 0) {
			if (value[0] != '/' || strlen(value) >= sizeof(cfg->adc_device)) {
				log_msg(PRI_ERR, "%s:%u: adc_device must be an absolute path",
					path, lineno);
				ret = -1;
				break;
			}
			snprintf(cfg->adc_device, sizeof(cfg->adc_device), "%s", value);
		} else if (strcmp(key, "display_connector") == 0) {
			if (!display_connector_valid(value) ||
			    strlen(value) >= sizeof(cfg->display_connector)) {
				log_msg(PRI_ERR, "%s:%u: invalid display_connector '%s' "
					"(a name from /sys/class/drm, such as SPI-1)",
					path, lineno, value);
				ret = -1;
				break;
			}
			snprintf(cfg->display_connector, sizeof(cfg->display_connector),
				 "%s", value);
		} else if (strncmp(key, "channel.", 8) == 0) {
			if (add_channel(cfg, key + 8, value) < 0) {
				log_msg(PRI_ERR, "%s:%u: invalid or duplicate channel '%s = %s' "
					"(names [a-z0-9_-], index 0-%d, at most %d channels)",
					path, lineno, key + 8, value, MAX_IIO_CHANNEL, MAX_CHANNELS);
				ret = -1;
				break;
			}
		} else {
			log_msg(PRI_WARN, "%s:%u: unknown key '%s', ignored",
				path, lineno, key);
		}
	}

	fclose(f);
	return ret;
}

/* Appends to buf, keeping it terminated; returns -1 once it is full. */
__attribute__((format(printf, 4, 5)))
static int append(char *buf, size_t size, size_t *used, const char *fmt, ...)
{
	va_list ap;
	int n;

	if (*used >= size)
		return -1;
	va_start(ap, fmt);
	n = vsnprintf(buf + *used, size - *used, fmt, ap);
	va_end(ap);
	if (n < 0 || (size_t)n >= size - *used) {
		*used = size;
		return -1;
	}
	*used += (size_t)n;
	return 0;
}

/*
 * {"sensors":{"<name>":{"channel":N,"raw":R,"millivolts":M}, ...}}
 * A channel that cannot be read reports "error" instead of a value.
 */
static int sensors_json(const struct config *cfg, char *buf, size_t size)
{
	struct reading readings[MAX_CHANNELS];
	size_t used = 0;

	read_sensors(cfg->adc_device, cfg->channels, cfg->num_channels, readings);

	append(buf, size, &used, "{\"sensors\":{");
	for (unsigned int i = 0; i < cfg->num_channels; i++) {
		const struct reading *r = &readings[i];

		append(buf, size, &used, "%s\"%s\":{\"channel\":%u,",
		       i ? "," : "", r->channel->name, r->channel->index);
		switch (r->status) {
		case READING_NO_DEVICE:
			append(buf, size, &used, "\"error\":\"no IIO device\"}");
			break;
		case READING_FAILED:
			append(buf, size, &used, "\"error\":\"read failed\"}");
			break;
		case READING_OK:
			append(buf, size, &used, "\"raw\":%ld,\"millivolts\":%.3f}",
			       r->raw, r->millivolts);
			break;
		}
	}
	return append(buf, size, &used, "}}\n");
}

static int listen_on(const struct config *cfg)
{
	struct sockaddr_in sa = {
		.sin_family = AF_INET,
		.sin_port = htons(cfg->port),
	};
	int one = 1;
	int fd;

	if (inet_pton(AF_INET, cfg->bind, &sa.sin_addr) != 1) {
		log_msg(PRI_ERR, "Invalid bind address '%s'", cfg->bind);
		return -1;
	}

	fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
	if (fd < 0) {
		log_msg(PRI_ERR, "socket: %s", strerror(errno));
		return -1;
	}
	if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) < 0)
		log_msg(PRI_WARN, "SO_REUSEADDR: %s", strerror(errno));

	if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0 ||
	    listen(fd, 16) < 0) {
		log_msg(PRI_ERR, "Cannot listen on %s:%u: %s",
			cfg->bind, cfg->port, strerror(errno));
		close(fd);
		return -1;
	}
	return fd;
}

static void send_all(int fd, const char *buf, size_t len)
{
	while (len > 0) {
		ssize_t n = send(fd, buf, len, MSG_NOSIGNAL);

		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0)
			return;
		buf += n;
		len -= (size_t)n;
	}
}

static void respond(int fd, int status, const char *reason,
		    const char *extra_headers, const char *body)
{
	char head[256];
	int n;

	n = snprintf(head, sizeof(head),
		     "HTTP/1.1 %d %s\r\n"
		     "Content-Type: application/json\r\n"
		     "Content-Length: %zu\r\n"
		     "Connection: close\r\n"
		     "%s"
		     "\r\n",
		     status, reason, strlen(body),
		     extra_headers ? extra_headers : "");
	if (n < 0 || (size_t)n >= sizeof(head))
		return;
	send_all(fd, head, (size_t)n);
	send_all(fd, body, strlen(body));
}

static void route(int fd, char *request, const struct config *cfg,
		  long long start_ms)
{
	char method[8];
	char path[256];
	char body[256];
	char *query;

	if (sscanf(request, "%7s %255s", method, path) != 2) {
		respond(fd, 400, "Bad Request", NULL,
			"{\"error\":\"bad request\"}\n");
		return;
	}
	if (strcmp(method, "GET") != 0) {
		respond(fd, 405, "Method Not Allowed", "Allow: GET\r\n",
			"{\"error\":\"method not allowed\"}\n");
		return;
	}

	query = strchr(path, '?');
	if (query)
		*query = '\0';

	if (strcmp(path, "/health") == 0) {
		snprintf(body, sizeof(body),
			 "{\"status\":\"ok\",\"version\":\"%s\",\"uptime_s\":%lld}\n",
			 EDGENODED_VERSION, (now_ms() - start_ms) / 1000);
		respond(fd, 200, "OK", NULL, body);
	} else if (strcmp(path, "/version") == 0) {
		snprintf(body, sizeof(body), "{\"version\":\"%s\"}\n",
			 EDGENODED_VERSION);
		respond(fd, 200, "OK", NULL, body);
	} else if (strcmp(path, "/sensors") == 0) {
		char sensors[2048];

		if (sensors_json(cfg, sensors, sizeof(sensors)) < 0)
			respond(fd, 500, "Internal Server Error", NULL,
				"{\"error\":\"response too large\"}\n");
		else
			respond(fd, 200, "OK", NULL, sensors);
	} else {
		respond(fd, 404, "Not Found", NULL,
			"{\"error\":\"not found\"}\n");
	}
}

/*
 * One request per connection, served inline. A slow client holds the loop
 * for at most CLIENT_TIMEOUT_S per direction, well inside the watchdog
 * interval.
 */
static void handle_client(int listen_fd, const struct config *cfg, long long start_ms)
{
	struct timeval timeout = { .tv_sec = CLIENT_TIMEOUT_S };
	char request[REQUEST_MAX + 1];
	size_t used = 0;
	int fd;

	fd = accept4(listen_fd, NULL, NULL, SOCK_CLOEXEC);
	if (fd < 0) {
		if (errno != EINTR && errno != EAGAIN && errno != ECONNABORTED)
			log_msg(PRI_WARN, "accept: %s", strerror(errno));
		return;
	}
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

	/* Read up to the end of the headers; no endpoint takes a body. */
	request[0] = '\0';
	while (used < REQUEST_MAX) {
		ssize_t n = recv(fd, request + used, REQUEST_MAX - used, 0);

		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0)
			break;
		used += (size_t)n;
		request[used] = '\0';
		if (strstr(request, "\r\n\r\n"))
			break;
	}

	route(fd, request, cfg, start_ms);
	close(fd);
}

/*
 * The display, when one is configured. It is opened once its connector is
 * there (the panel's driver may load after the daemon starts) and again
 * after an error, and redrawn every SCREEN_PERIOD_MS when what it would
 * show has changed.
 */
struct screen_output {
	struct display display;
	bool open;
	bool drawn;
	struct screen shown;
	char last_error[256];	/* logged once until it changes */
	long long next_ms;
};

static void update_screen(struct screen_output *out, const struct config *cfg)
{
	struct reading readings[MAX_CHANNELS];
	char hostname[65] = "";
	struct screen next;
	char err[sizeof(out->last_error)];

	if (!out->open) {
		if (display_open(&out->display, cfg->display_connector,
				 err, sizeof(err)) < 0) {
			if (strcmp(err, out->last_error) != 0)
				log_msg(PRI_WARN, "Display not ready (%s), retrying", err);
			snprintf(out->last_error, sizeof(out->last_error), "%s", err);
			return;
		}
		out->open = true;
		out->drawn = false;
		out->last_error[0] = '\0';
		log_msg(PRI_INFO, "Showing readings on %s %s (%ux%u)",
			out->display.card, cfg->display_connector,
			out->display.canvas.width, out->display.canvas.height);
	}

	read_sensors(cfg->adc_device, cfg->channels, cfg->num_channels, readings);
	if (gethostname(hostname, sizeof(hostname) - 1) < 0)
		snprintf(hostname, sizeof(hostname), "edgenode");
	screen_build(&next, hostname, readings, cfg->num_channels);
	if (out->drawn && memcmp(&next, &out->shown, sizeof(next)) == 0)
		return;

	screen_draw(&out->display.canvas, &next);
	if (display_flush(&out->display) < 0) {
		log_msg(PRI_WARN, "Display %s: %s, reopening",
			cfg->display_connector, strerror(errno));
		display_close(&out->display);
		out->open = false;
		return;
	}
	out->shown = next;
	out->drawn = true;
}

static void on_signal(int sig)
{
	(void)sig;
	stop_requested = 1;
}

static int setup_signals(void)
{
	struct sigaction sa = { .sa_handler = on_signal };

	/* No SA_RESTART: poll() must return EINTR so the loop sees the stop. */
	sigemptyset(&sa.sa_mask);
	if (sigaction(SIGTERM, &sa, NULL) < 0 || sigaction(SIGINT, &sa, NULL) < 0)
		return -1;
	signal(SIGPIPE, SIG_IGN);
	return 0;
}

static void usage(FILE *out)
{
	fprintf(out,
		"Usage: edgenoded [--config PATH] [--port PORT]\n"
		"\n"
		"  -c, --config PATH  configuration file (default " DEFAULT_CONFIG ")\n"
		"  -p, --port PORT    HTTP port, overrides the configuration file\n"
		"  -V, --version      print the version and exit\n"
		"  -h, --help         show this help\n");
}

int main(int argc, char **argv)
{
	static const struct option options[] = {
		{ "config",  required_argument, NULL, 'c' },
		{ "port",    required_argument, NULL, 'p' },
		{ "version", no_argument,       NULL, 'V' },
		{ "help",    no_argument,       NULL, 'h' },
		{ NULL, 0, NULL, 0 },
	};
	struct config cfg = { .bind = DEFAULT_BIND, .port = DEFAULT_PORT };
	const char *config_path = DEFAULT_CONFIG;
	bool config_required = false;
	const char *port_arg = NULL;
	struct screen_output screen = { .display = { .fd = -1 } };
	bool show_screen;
	struct pollfd pfd;
	char status[128];
	long long start_ms, next_ping = 0;
	int watchdog_ms, opt;

	while ((opt = getopt_long(argc, argv, "c:p:Vh", options, NULL)) != -1) {
		switch (opt) {
		case 'c':
			config_path = optarg;
			config_required = true;
			break;
		case 'p':
			port_arg = optarg;
			break;
		case 'V':
			printf("edgenoded %s\n", EDGENODED_VERSION);
			return EXIT_SUCCESS;
		case 'h':
			usage(stdout);
			return EXIT_SUCCESS;
		default:
			usage(stderr);
			return EXIT_FAILURE;
		}
	}
	if (optind < argc) {
		usage(stderr);
		return EXIT_FAILURE;
	}

	if (load_config(config_path, config_required, &cfg) < 0)
		return EXIT_FAILURE;
	if (port_arg && parse_port(port_arg, &cfg.port) < 0) {
		log_msg(PRI_ERR, "Invalid port '%s'", port_arg);
		return EXIT_FAILURE;
	}
	if (setup_signals() < 0) {
		log_msg(PRI_ERR, "sigaction: %s", strerror(errno));
		return EXIT_FAILURE;
	}

	pfd.fd = listen_on(&cfg);
	if (pfd.fd < 0)
		return EXIT_FAILURE;
	pfd.events = POLLIN;

	start_ms = now_ms();
	watchdog_ms = watchdog_interval_ms();
	if (watchdog_ms > 0)
		next_ping = start_ms + watchdog_ms;
	show_screen = cfg.display_connector[0] != '\0';
	screen.next_ms = start_ms;

	log_msg(PRI_INFO, "edgenoded %s listening on %s:%u",
		EDGENODED_VERSION, cfg.bind, cfg.port);
	if (cfg.num_channels)
		log_msg(PRI_INFO, "Reading %u analog input(s) from %s",
			cfg.num_channels, cfg.adc_device[0] ? cfg.adc_device : "(no adc_device)");
	if (show_screen)
		log_msg(PRI_INFO, "Showing readings on display connector %s",
			cfg.display_connector);
	snprintf(status, sizeof(status),
		 "READY=1\nSTATUS=Serving health on %s:%u", cfg.bind, cfg.port);
	notify(status);

	while (!stop_requested) {
		long long now = now_ms();
		int timeout = -1;
		int ready;

		if (watchdog_ms > 0) {
			long long left = next_ping - now;

			timeout = left > 0 ? (int)left : 0;
		}
		if (show_screen) {
			long long left = screen.next_ms - now;
			int wait = left > 0 ? (int)left : 0;

			if (timeout < 0 || wait < timeout)
				timeout = wait;
		}

		ready = poll(&pfd, 1, timeout);
		if (ready < 0) {
			if (errno == EINTR)
				continue;
			log_msg(PRI_ERR, "poll: %s", strerror(errno));
			break;
		}

		if (watchdog_ms > 0 && now_ms() >= next_ping) {
			notify("WATCHDOG=1");
			next_ping = now_ms() + watchdog_ms;
		}
		if (ready > 0 && (pfd.revents & POLLIN))
			handle_client(pfd.fd, &cfg, start_ms);
		if (show_screen && now_ms() >= screen.next_ms) {
			update_screen(&screen, &cfg);
			screen.next_ms = now_ms() + SCREEN_PERIOD_MS;
		}
	}

	notify("STOPPING=1");
	log_msg(PRI_INFO, "Shutting down");
	if (screen.open)
		display_close(&screen.display);
	close(pfd.fd);
	return stop_requested ? EXIT_SUCCESS : EXIT_FAILURE;
}
