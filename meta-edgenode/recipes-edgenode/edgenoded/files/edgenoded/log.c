// SPDX-License-Identifier: MIT

#include <stdarg.h>
#include <stdio.h>

#include "log.h"

void log_msg(const char *priority, const char *fmt, ...)
{
	char line[1024];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	/* One call: stdio locks the stream for its duration. */
	fprintf(stderr, "%s%s\n", priority, line);
}
