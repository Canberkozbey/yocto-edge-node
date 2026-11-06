/* SPDX-License-Identifier: MIT */
#ifndef EDGENODED_LOG_H
#define EDGENODED_LOG_H

/* journald reads a leading <N> as the syslog priority of the line. */
#define PRI_ERR  "<3>"
#define PRI_WARN "<4>"
#define PRI_INFO "<6>"

/* One line to stderr, written whole, so lines from threads do not mix. */
__attribute__((format(printf, 2, 3)))
void log_msg(const char *priority, const char *fmt, ...);

#endif
