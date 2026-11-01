// SPDX-License-Identifier: MIT
/*
 * edgenoded - edge node daemon
 *
 * Serves the node's health over HTTP and reports its state to systemd:
 * readiness, watchdog keep-alives and shutdown. Later stages add sensor
 * readings and MQTT publishing.
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

#ifndef EDGENODED_VERSION
#define EDGENODED_VERSION "0.0.0-dev"
#endif

#define DEFAULT_CONFIG   "/etc/edgenoded.conf"
#define DEFAULT_BIND     "0.0.0.0"
#define DEFAULT_PORT     8080
#define REQUEST_MAX      4096
#define CLIENT_TIMEOUT_S 2

/* journald reads a leading <N> as the syslog priority of the line. */
#define PRI_ERR  "<3>"
#define PRI_WARN "<4>"
#define PRI_INFO "<6>"

struct config {
	char bind[INET_ADDRSTRLEN];
	unsigned short port;
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
		} else {
			log_msg(PRI_WARN, "%s:%u: unknown key '%s', ignored",
				path, lineno, key);
		}
	}

	fclose(f);
	return ret;
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

static void route(int fd, char *request, long long start_ms)
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
static void handle_client(int listen_fd, long long start_ms)
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

	route(fd, request, start_ms);
	close(fd);
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

	log_msg(PRI_INFO, "edgenoded %s listening on %s:%u",
		EDGENODED_VERSION, cfg.bind, cfg.port);
	snprintf(status, sizeof(status),
		 "READY=1\nSTATUS=Serving health on %s:%u", cfg.bind, cfg.port);
	notify(status);

	while (!stop_requested) {
		int timeout = -1;
		int ready;

		if (watchdog_ms > 0) {
			long long left = next_ping - now_ms();

			timeout = left > 0 ? (int)left : 0;
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
			handle_client(pfd.fd, start_ms);
	}

	notify("STOPPING=1");
	log_msg(PRI_INFO, "Shutting down");
	close(pfd.fd);
	return stop_requested ? EXIT_SUCCESS : EXIT_FAILURE;
}
